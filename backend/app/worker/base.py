"""Base Celery task that keeps the ``task_run`` record in sync."""

import logging
import threading
import traceback
from datetime import UTC, datetime
from typing import Any

from celery import Task
from fastapi.encoders import jsonable_encoder
from redis.lock import Lock
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql.expression import bindparam

from app.core.db import get_engine
from app.core.redis import get_redis
from app.worker.progress import (
    HEARTBEAT_INTERVAL_SECONDS,
    HEARTBEAT_TTL_SECONDS,
    ProgressReporter,
    TaskCancelled,
    TaskFailed,
    cancel_key,
    heartbeat_key,
)

logger = logging.getLogger(__name__)


def lock_key(name: str) -> str:
    return f"lock:{name}"


class _Heartbeat:
    """Background thread that proves the task is alive and keeps its lock from expiring.

    Locks and heartbeats have a short TTL, so a worker that dies (OOM, SIGKILL) frees them within
    ``HEARTBEAT_TTL_SECONDS`` instead of blocking the next run for hours.
    """

    def __init__(self, task_id: str, lock: Lock | None):
        self.task_id = task_id
        self.lock = lock
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"heartbeat-{task_id}", daemon=True)

    def beat(self) -> None:
        get_redis().set(heartbeat_key(self.task_id), "1", ex=HEARTBEAT_TTL_SECONDS)
        if self.lock is not None:
            self.lock.extend(HEARTBEAT_TTL_SECONDS, replace_ttl=True)

    def _run(self) -> None:
        while not self._stop.wait(HEARTBEAT_INTERVAL_SECONDS):
            try:
                self.beat()
            except Exception:
                logger.warning("Heartbeat for task %s failed", self.task_id, exc_info=True)

    def __enter__(self) -> "_Heartbeat":
        self.beat()
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join(timeout=5)
        try:
            get_redis().delete(heartbeat_key(self.task_id))
        except Exception:
            logger.warning("Could not clear the heartbeat of task %s", self.task_id, exc_info=True)


class TrackedTask(Task):
    """Every task receives ``self.progress`` and has its lifecycle recorded in ``task_run``.

    Subclasses may set ``lock_name``: only one task holding a given lock runs at a time.
    A task signals partial failure by raising ``TaskFailed(message, result)``: it ends as FAILURE
    but keeps its result (e.g. which tables did import).
    """

    abstract = True
    lock_name: str | None = None

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        task_id = self.request.id
        if task_id is None:  # direct function call (tests)
            return self.run(*args, **kwargs)
        lock = None
        if self.lock_name:
            # thread_local=False: the heartbeat thread extends a lock acquired by this thread
            lock = get_redis().lock(
                lock_key(self.lock_name), timeout=HEARTBEAT_TTL_SECONDS, thread_local=False
            )
            if not lock.acquire(blocking=False):
                _finish(
                    task_id, "FAILURE", error=f"Another '{self.lock_name}' task is already running"
                )
                return None
        try:
            with _Heartbeat(task_id, lock):
                _set_status(task_id, "STARTED", started=True)
                self.progress = ProgressReporter(task_id)
                return self._run_tracked(task_id, *args, **kwargs)
        finally:
            if lock is not None:
                try:
                    lock.release()
                except Exception:
                    logger.warning("Could not release lock %s", self.lock_name, exc_info=True)
            try:
                get_redis().delete(cancel_key(task_id))
            except Exception:
                logger.warning("Could not clear the cancel flag of %s", task_id, exc_info=True)

    def _run_tracked(self, task_id: str, *args: Any, **kwargs: Any) -> Any:
        try:
            result = self.run(*args, **kwargs)
        except TaskCancelled as exc:
            _finish(task_id, "CANCELLED", message=str(exc) or "Cancelled by user")
            return None
        except TaskFailed as exc:
            logger.warning("Task %s (%s) failed: %s", self.name, task_id, exc)
            _finish(task_id, "FAILURE", result=exc.result, error=str(exc))
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
