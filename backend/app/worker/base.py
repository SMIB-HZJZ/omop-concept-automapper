"""Base Celery task that keeps the ``task_run`` record in sync."""

import logging
import traceback
from datetime import UTC, datetime
from typing import Any

from celery import Task
from fastapi.encoders import jsonable_encoder
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql.expression import bindparam

from app.core.db import get_engine
from app.core.redis import get_redis
from app.worker.progress import ProgressReporter, TaskCancelled, cancel_key

logger = logging.getLogger(__name__)


class TrackedTask(Task):
    """Every task receives ``self.progress`` and has its lifecycle recorded in ``task_run``.

    Subclasses may set ``lock_name``: only one task holding a given lock runs at a time.
    """

    abstract = True
    lock_name: str | None = None
    lock_timeout = 24 * 3600

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        task_id = self.request.id
        if task_id is None:  # direct function call (tests)
            return self.run(*args, **kwargs)
        _set_status(task_id, "STARTED", started=True)
        self.progress = ProgressReporter(task_id)
        lock = None
        if self.lock_name:
            lock = get_redis().lock(f"lock:{self.lock_name}", timeout=self.lock_timeout)
            if not lock.acquire(blocking=False):
                _finish(
                    task_id, "FAILURE", error=f"Another '{self.lock_name}' task is already running"
                )
                return None
        try:
            result = self.run(*args, **kwargs)
        except TaskCancelled as exc:
            _finish(task_id, "CANCELLED", message=str(exc) or "Cancelled by user")
            return None
        except Exception as exc:
            logger.exception("Task %s (%s) failed", self.name, task_id)
            _finish(
                task_id,
                "FAILURE",
                error=f"{type(exc).__name__}: {exc}",
                trace=traceback.format_exc(),
            )
            return None
        finally:
            if lock is not None:
                try:
                    lock.release()
                except Exception:
                    pass
            try:
                get_redis().delete(cancel_key(task_id))
            except Exception:
                pass
        _finish(task_id, "SUCCESS", result=result)
        return result


def _set_status(task_id: str, status: str, started: bool = False) -> None:
    with get_engine().begin() as conn:
        conn.execute(
            text(
                "UPDATE task_run SET status = :s"
                + (", started_at = now()" if started else "")
                + " WHERE id = :id AND status NOT IN ('CANCELLED', 'SUCCESS', 'FAILURE')"
            ),
            {"s": status, "id": task_id},
        )


def _finish(
    task_id: str,
    status: str,
    result: Any = None,
    error: str | None = None,
    message: str | None = None,
    trace: str | None = None,
) -> None:
    if trace:
        logger.debug(trace)
    payload = jsonable_encoder(result) if result is not None else None
    if payload is not None and not isinstance(payload, dict):
        payload = {"value": payload}
    stmt = text(
        """
        UPDATE task_run
        SET status = :s, result = :r, error = :e, finished_at = :now,
            message = COALESCE(:m, message),
            progress_current = CASE WHEN :s = 'SUCCESS' AND progress_total IS NOT NULL
                                    THEN progress_total ELSE progress_current END
        WHERE id = :id
        """
    ).bindparams(bindparam("r", type_=JSONB))
    with get_engine().begin() as conn:
        conn.execute(
            stmt,
            {
                "s": status,
                "r": payload,
                "e": error,
                "m": message,
                "now": datetime.now(UTC),
                "id": task_id,
            },
        )
