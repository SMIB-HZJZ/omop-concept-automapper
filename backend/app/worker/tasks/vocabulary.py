import logging
import os

from sqlmodel import select

from app.core import cache
from app.core.config import get_settings
from app.core.db import raw_connection, session_scope
from app.models import AppUser, EmbeddingCollection
from app.services import mapping
from app.services import tasks as task_service
from app.services.atc7 import process_drug_atc7_codes
from app.services.settings_store import get_effective_config
from app.services.vocabulary_import import import_all
from app.vector import store
from app.worker.base import TrackedTask
from app.worker.celery_app import celery_app
from app.worker.progress import TaskFailed

logger = logging.getLogger(__name__)


def _sync_collections(conn) -> dict:
    """Every collection: drop vectors of concepts that left the standard set, move labels of
    concepts that changed domain, and flag vectors whose embedded text changed."""
    with session_scope() as session:
        collections = [
            (col.table_name, col.text_mode)
            for col in session.exec(select(EmbeddingCollection)).all()
            if store.collection_exists(session, col.name)
        ]
    pruned = relabeled = changed = 0
    for table, text_mode in collections:
        pruned += store.prune_non_standard(conn, table)
        relabeled += store.refresh_labels(conn, table)
        changed += store.mark_changed_text(conn, table, text_mode)
    return {"pruned": pruned, "relabeled": relabeled, "changed": changed}


def _refresh_active_collection(conn, user_id: int | None) -> dict:
    """Start re-embedding changed and new concepts of the active collection (other collections
    are refreshed when they are next embedded)."""
    with session_scope() as session:
        cfg = get_effective_config(session)
        col = store.get_collection(session, cfg.collection_name)
        if col is None or not store.collection_exists(session, col.name):
            return {"changed": 0, "new": 0, "message": "No active embedding collection"}
        table = col.table_name
    pending = store.refresh_pending(conn, table)
    if not pending["changed"] and not pending["new"]:
        return {**pending, "message": "Embeddings are up to date"}
    if not get_settings().auto_embed_after_vocabulary_import:
        return {
            **pending,
            "message": "Automatic re-embedding is off; start it on the Embeddings tab",
        }
    # Imported lazily: the embedding task module imports this package's siblings
    from app.worker.tasks.embeddings import embed_standard_concepts

    with session_scope() as session:
        user = session.get(AppUser, user_id) if user_id else None
        try:
            run = task_service.launch(
                session, "embeddings.embed", embed_standard_concepts, {"refresh_only": True}, user
            )
        except (task_service.TaskConflict, task_service.TaskLaunchError) as exc:
            logger.warning("Could not start re-embedding after the vocabulary import: %s", exc)
            return {**pending, "message": f"Re-embedding not started: {exc}"}
    return {**pending, "task_id": run.id, "message": "Re-embedding started"}


@celery_app.task(bind=True, base=TrackedTask, name="vocabulary.import_all", lock_name="vocab")
def import_vocabulary(
    self, path: str, filename: str | None = None, user_id: int | None = None
) -> dict:
    """Import an uploaded Athena zip; the task owns the file and deletes it when done."""
    try:
        return _import_vocabulary(self, path, user_id)
    finally:
        if os.path.exists(path):
            os.remove(path)


def _import_vocabulary(self, path: str, user_id: int | None) -> dict:
    with raw_connection() as conn:
        results = import_all(conn, path, self.progress, task_id=self.request.id)
        self.progress.phase("Updating mappings to concepts that are no longer standard")
        stale = mapping.update_non_standard_mappings(conn)
        conn.commit()
        # Vectors of concepts that left the standard set go, and domain labels (used by the
        # filtered vector search) follow concepts that changed domain
        imported = {t for t, r in results.items() if r["status"] == "success"}
        # ATC7 codes of drugs (used to narrow drug searches) come from these three tables, so
        # they are recomputed after every import that changed one of them
        atc7 = None
        if imported & {"concept", "concept_relationship", "concept_ancestor"}:
            self.progress.phase("Computing ATC7 codes of drugs")
            atc7 = process_drug_atc7_codes(conn)
        self.progress.phase("Updating embedding collections")
        vectors = _sync_collections(conn)
    with session_scope() as session:
        mapping.invalidate_all_mappings(session)
    cache.invalidate("vocab", "atc7", "embeddings")
    with raw_connection() as conn:
        refresh = _refresh_active_collection(conn, user_id)
    succeeded = sum(1 for r in results.values() if r["status"] == "success")
    summary = {
        "tables": results,
        "tables_imported": succeeded,
        "records_imported": sum(r["records_imported"] for r in results.values()),
        **stale,
        "vectors_pruned": vectors["pruned"],
        "vectors_relabeled": vectors["relabeled"],
        "vectors_text_changed": vectors["changed"],
        "drugs_with_atc7": atc7,
        "embedding_refresh": refresh,
    }
    failed = [t for t, r in results.items() if r["status"] == "failed"]
    if failed:
        raise TaskFailed(f"Import failed for: {', '.join(failed)}", summary)
    return summary
