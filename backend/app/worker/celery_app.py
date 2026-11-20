"""Celery application. Redis is both broker and result backend."""

import logging
import os

from celery import Celery
from celery.signals import after_setup_logger, worker_process_init, worker_ready

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "omop_automapper",
    broker=settings.broker_url,
    backend=settings.result_backend,
    include=[
        "app.worker.tasks.vocabulary",
        "app.worker.tasks.source",
        "app.worker.tasks.embeddings",
        "app.worker.tasks.automap",
    ],
)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    worker_prefetch_multiplier=1,
    result_expires=24 * 3600,
    broker_connection_retry_on_startup=True,
    task_always_eager=os.getenv("CELERY_TASK_ALWAYS_EAGER", "").lower() in ("1", "true"),
    task_eager_propagates=False,
    worker_hijack_root_logger=False,
)


@after_setup_logger.connect
def _setup_logging(**_):
    from app.core.logging import setup_logging

    setup_logging()


@worker_process_init.connect
def _reset_db_after_fork(**_):
    # Pooled connections must not be shared across forked processes
    from app.core.db import reset_engine

    reset_engine()


@worker_ready.connect
def _fail_orphaned_tasks(**_):
    """Tasks left running by a previous worker process can never finish; mark them failed."""
    from app.services.tasks import fail_orphaned_tasks

    try:
        count = fail_orphaned_tasks()
        if count:
            logging.getLogger(__name__).warning("Marked %d orphaned task(s) as failed", count)
    except Exception:
        logging.getLogger(__name__).exception("Could not clean up orphaned tasks")
