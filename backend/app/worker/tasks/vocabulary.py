from sqlmodel import select

from app.core import cache
from app.core.db import raw_connection, session_scope
from app.models import EmbeddingCollection
from app.services import mapping
from app.services.vocabulary_import import import_all
from app.vector import store
from app.worker.base import TrackedTask
from app.worker.celery_app import celery_app
from app.worker.progress import TaskFailed


def _sync_collections(conn) -> dict:
    with session_scope() as session:
        tables = [
            col.table_name
            for col in session.exec(select(EmbeddingCollection)).all()
            if store.collection_exists(session, col.name)
        ]
    pruned = relabeled = 0
    for table in tables:
        pruned += store.prune_non_standard(conn, table)
        relabeled += store.refresh_labels(conn, table)
    return {"pruned": pruned, "relabeled": relabeled}


@celery_app.task(bind=True, base=TrackedTask, name="vocabulary.import_all", lock_name="vocab")
def import_vocabulary(self, user_id: int | None = None) -> dict:
    with raw_connection() as conn:
        results = import_all(conn, self.progress, task_id=self.request.id)
        self.progress.phase("Updating mappings to concepts that are no longer standard")
        stale = mapping.update_non_standard_mappings(conn)
        conn.commit()
        # Vectors of concepts that left the standard set go, and domain labels (used by the
        # filtered vector search) follow concepts that changed domain
        self.progress.phase("Updating embedding collections")
        vectors = _sync_collections(conn)
    with session_scope() as session:
        mapping.invalidate_all_mappings(session)
    cache.invalidate("vocab", "atc7", "embeddings")
    succeeded = sum(1 for r in results.values() if r["status"] == "success")
    summary = {
        "tables": results,
        "tables_imported": succeeded,
        "records_imported": sum(r["records_imported"] for r in results.values()),
        **stale,
        "vectors_pruned": vectors["pruned"],
        "vectors_relabeled": vectors["relabeled"],
    }
    failed = [t for t, r in results.items() if r["status"] == "failed"]
    if failed:
        raise TaskFailed(f"Import failed for: {', '.join(failed)}", summary)
    return summary
