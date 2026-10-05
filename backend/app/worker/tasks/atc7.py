from app.core import cache
from app.core.db import raw_connection
from app.services.atc7 import process_drug_atc7_codes
from app.worker.base import TrackedTask
from app.worker.celery_app import celery_app


@celery_app.task(bind=True, base=TrackedTask, name="atc7.process", lock_name="vocab")
def process_atc7(self, user_id: int | None = None) -> dict:
    self.progress.phase("Finding ATC7 ancestors of standard drug concepts", total=1)
    with raw_connection() as conn:
        count = process_drug_atc7_codes(conn)
    self.progress.update(current=1, message="Done", force=True)
    cache.invalidate("atc7")
    return {"drugs_with_atc7": count}
