"""Embedding tasks: embed standard concepts, keep metadata in sync, copy vectors from Qdrant."""

import logging

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


def _active_collection(probe: bool):
    with session_scope() as session:
        cfg = get_effective_config(session)
        embedder = Embedder(cfg.embedding_model, cfg.embedding_dims)
        col = store.get_collection(session, cfg.collection_name)
        if col is None and probe:
            dims = embedder.probe_dims()
            col = store.ensure_collection(session, cfg.collection_name, cfg.embedding_model, dims)
        elif col is not None:
            col = store.ensure_collection(session, col.name, col.model, col.dims)
        return cfg, embedder, col


@celery_app.task(bind=True, base=TrackedTask, name="embeddings.embed_standard", lock_name="embed")
def embed_standard_concepts(
    self, domain: str | None = None, batch_size: int = 1000, user_id: int | None = None
) -> dict:
    progress = self.progress
    progress.phase("Checking the embedding model")
    cfg, embedder, col = _active_collection(probe=True)
    table = col.table_name

    with raw_connection() as conn:
        store.ensure_domain_labels(conn)

    domain_sql = " AND c.domain_id = %s" if domain else ""
    base_query = f"""
        FROM concept c
        JOIN domain_label dl ON dl.domain_id = c.domain_id
        LEFT JOIN {table} e ON e.concept_id = c.concept_id
        WHERE c.standard_concept = 'S' AND e.concept_id IS NULL
          AND {store.EXCLUDED_CLASS_FILTER}{domain_sql}
    """
    params = (domain,) if domain else ()

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
        if rebuild:
            progress.phase("Dropping the DiskANN index for the bulk load")
            store.drop_index(write_conn, table)

        progress.phase(f"Embedding {pending:,} concepts with {cfg.embedding_model}", total=pending)
        embedded = 0
        try:
            with read_conn.transaction(), read_conn.cursor(name="pending_concepts") as cur:
                cur.itersize = batch_size
                cur.execute(
                    "SELECT c.concept_id, c.concept_name, dl.label "
                    + base_query
                    + " ORDER BY c.concept_id",
                    params,
                )
                while batch := cur.fetchmany(batch_size):
                    vectors = embedder.embed([row[1] for row in batch])
                    embedded += store.bulk_upsert(
                        write_conn,
                        table,
                        ((row[0], vec, [row[2]]) for row, vec in zip(batch, vectors, strict=True)),
                    )
                    progress.update(
                        current=embedded, message=f"Embedded {embedded:,}/{pending:,} concepts"
                    )
                    progress.check_cancelled()
        finally:
            # Always leave a usable index behind, even after a cancel or an error
            progress.phase("Building the DiskANN index")
            store.create_index(write_conn, table)
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
