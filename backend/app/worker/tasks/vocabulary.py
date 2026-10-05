from app.core import cache
from app.core.db import raw_connection, session_scope
from app.services import mapping
from app.services.vocabulary_import import import_all
from app.worker.base import TrackedTask
from app.worker.celery_app import celery_app


@celery_app.task(bind=True, base=TrackedTask, name="vocabulary.import_all", lock_name="vocab")
def import_vocabulary(self, user_id: int | None = None) -> dict:
    with raw_connection() as conn:
        results = import_all(conn, self.progress, task_id=self.request.id)
        self.progress.phase("Removing mappings to concepts that are no longer standard")
        removed = mapping.clean_non_standard_mappings(conn)
        conn.commit()
    with session_scope() as session:
        mapping.invalidate_all_mappings(session)
    cache.invalidate("vocab", "atc7", "embeddings")
    succeeded = sum(1 for r in results.values() if r["status"] == "success")
    return {
        "tables": results,
        "tables_imported": succeeded,
        "records_imported": sum(r["records_imported"] for r in results.values()),
        "non_standard_mappings_removed": removed,
    }
