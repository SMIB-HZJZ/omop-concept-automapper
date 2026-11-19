"""Embedding tasks: embed standard concepts and keep their metadata in sync."""

import hashlib
import logging
from concurrent.futures import Future, ThreadPoolExecutor

from sqlalchemy import text

from app.core import cache
from app.core.config import get_settings
from app.core.db import raw_connection, session_scope
from app.llm.embeddings import Embedder
from app.services.settings_store import get_effective_config
from app.vector import store
from app.worker.base import TrackedTask
from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)


def _text_hash(text_: str) -> str:
    """Same as Postgres md5() of the text (UTF-8), see store.text_hash_sql."""
    return hashlib.md5(text_.encode("utf-8")).hexdigest()


def _active_collection(probe: bool):
    with session_scope() as session:
        cfg = get_effective_config(session)
        embedder = Embedder(cfg.embedding_model, cfg.embedding_dims, cfg.embedding_task_types)
        col = store.get_collection(session, cfg.collection_name)
        if col is None and probe:
            dims = embedder.probe_dims()
            col = store.ensure_collection(
                session,
                cfg.collection_name,
                cfg.embedding_model,
                dims,
                text_mode=cfg.embedding_text,
                task_types=cfg.embedding_task_types,
            )
        elif col is not None:
            col = store.ensure_collection(session, col.name, col.model, col.dims)
        return cfg, embedder, col


@celery_app.task(bind=True, base=TrackedTask, name="embeddings.embed_standard", lock_name="embed")
def embed_standard_concepts(
    self,
    domain: str | None = None,
    batch_size: int = 4000,
    refresh_only: bool = False,
    user_id: int | None = None,
    domains: list[str] | None = None,
) -> dict:
    """Embed standard concepts that have no vector yet, and re-embed those whose text changed.

    ``domains`` (or the older single ``domain``) limits the run; several domains in one run
    rebuild the DiskANN index once instead of once per domain. ``refresh_only`` (after a
    vocabulary update): only changed vectors and new concepts in domains the collection already
    covers, so a partly embedded vocabulary is not completed.

    The next batch is sent to the embedding model while the previous one is written."""
    progress = self.progress
    progress.phase("Checking the embedding model")
    cfg, embedder, col = _active_collection(probe=True)
    table = col.table_name

    with raw_connection() as conn:
        store.ensure_domain_labels(conn)

    wanted = sorted(set(domains or ([domain] if domain else [])))
    domain_sql = " AND c.domain_id = ANY(%s)" if wanted else ""
    refresh_sql = (
        f" AND (e.concept_id IS NOT NULL OR dl.label IN ({store.covered_labels_sql(table)}))"
        if refresh_only
        else ""
    )
    base_query = f"""
        FROM concept c
        JOIN domain_label dl ON dl.domain_id = c.domain_id
        LEFT JOIN {table} e ON e.concept_id = c.concept_id
        WHERE c.standard_concept = 'S' AND (e.concept_id IS NULL OR e.text_hash IS NULL)
          AND {store.EXCLUDED_CLASS_FILTER}{domain_sql}{refresh_sql}
    """
    params = (wanted,) if wanted else ()

    with raw_connection() as read_conn, raw_connection() as write_conn:
        with read_conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) " + base_query, params)
            pending = cur.fetchone()[0]
        read_conn.commit()
        if pending == 0:
            return {
                "collection": col.name,
                "embedded": 0,
                "message": "All concepts are already embedded",
            }

        rebuild = pending > get_settings().diskann_rebuild_threshold
        embedded = 0
        try:
            if rebuild:
                # Bulk loads are much faster without the index; the finally block rebuilds it
                progress.phase("Dropping the DiskANN index for the bulk load", cancellable=False)
                store.drop_index(write_conn, table)
            progress.phase(
                f"Embedding {pending:,} concepts with {cfg.embedding_model}", total=pending
            )
            with read_conn.transaction(), read_conn.cursor(name="pending_concepts") as cur:
                cur.itersize = batch_size
                cur.execute(
                    f"SELECT c.concept_id, {store.embedding_text_sql(col.text_mode)}, dl.label "
                    + base_query
                    + " ORDER BY c.concept_id",
                    params,
                )
                # One batch is embedded (in a thread) while the previous one is written
                with ThreadPoolExecutor(1, thread_name_prefix="embed-next") as pool:
                    previous: tuple[list, Future] | None = None
                    while True:
                        batch = cur.fetchmany(batch_size)
                        upcoming = (
                            (batch, pool.submit(embedder.embed_many, [row[1] for row in batch]))
                            if batch
                            else None
                        )
                        if previous is not None:
                            done_batch, future = previous
                            vectors = future.result()
                            embedded += store.bulk_upsert(
                                write_conn,
                                table,
                                (
                                    (row[0], vec, [row[2]], _text_hash(row[1]))
                                    for row, vec in zip(done_batch, vectors, strict=True)
                                ),
                            )
                            progress.update(
                                current=embedded,
                                message=f"Embedded {embedded:,}/{pending:,} concepts",
                            )
                            progress.check_cancelled()
                        if upcoming is None:
                            break
                        previous = upcoming
        finally:
            # Always leave a usable index behind, even after a cancel or an error. A failed batch
            # leaves write_conn in an aborted transaction, and the phase must not re-raise the
            # cancel (that would skip the index build).
            write_conn.rollback()
            progress.phase("Building the DiskANN index", cancellable=False)
            try:
                store.create_index(write_conn, table)
            except Exception:
                logger.exception("Could not build the DiskANN index on %s", table)
                write_conn.rollback()
            cache.invalidate("embeddings")

    return {"collection": col.name, "embedded": embedded, "pending_before": pending}


@celery_app.task(bind=True, base=TrackedTask, name="embeddings.sync_metadata", lock_name="embed")
def sync_metadata(self, user_id: int | None = None) -> dict:
    _, _, col = _active_collection(probe=False)
    if col is None:
        return {"removed": 0, "relabeled": 0, "message": "No embeddings in the current collection"}
    with raw_connection() as conn:
        self.progress.phase("Removing vectors of concepts that are no longer standard")
        removed = store.prune_non_standard(conn, col.table_name)
        self.progress.phase("Refreshing domain labels")
        relabeled = store.refresh_labels(conn, col.table_name)
    cache.invalidate("embeddings")
    return {
        "collection": col.name,
        "removed": removed,
        "relabeled": relabeled,
        "message": "Names, classes and ATC7 codes are read live from the vocabulary tables.",
    }


@celery_app.task(bind=True, base=TrackedTask, name="embeddings.sync_atc7", lock_name="embed")
def sync_atc7(self, user_id: int | None = None) -> dict:
    _, _, col = _active_collection(probe=False)
    if col is None:
        return {"embedded_with_atc7": 0, "message": "No embeddings in the current collection"}
    self.progress.phase("Checking ATC7 coverage of embedded concepts")
    with session_scope() as session:
        count = session.execute(
            text(
                f"SELECT COUNT(*) FROM {col.table_name} e JOIN concept_atc7 a ON a.concept_id = e.concept_id"
            )
        ).scalar_one()
    return {
        "collection": col.name,
        "embedded_with_atc7": count,
        "message": "ATC7 codes are joined live at search time, so they are always current.",
    }
