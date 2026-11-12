import logging
import os

from app.core.db import raw_connection
from app.services import mapping
from app.services.source_import import import_source_csv
from app.worker.base import TrackedTask
from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, base=TrackedTask, name="source.import_csv")
def import_source_concepts(
    self, path: str, vocabulary_id: int, filename: str | None = None, user_id: int | None = None
) -> dict:
    try:
        with raw_connection() as conn:
            result = import_source_csv(conn, path, vocabulary_id, self.progress, user_id=user_id)
    finally:
        try:
            os.remove(path)
        except OSError:
            logger.warning("Could not delete the uploaded file %s", path, exc_info=True)
    mapping.invalidate_vocabulary(vocabulary_id)
    return {**result, "vocabulary_id": vocabulary_id, "filename": filename}
