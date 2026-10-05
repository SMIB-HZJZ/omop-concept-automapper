"""Progress reporting and cooperative cancellation for background tasks."""

import time

from sqlalchemy import text

from app.core.db import get_engine
from app.core.redis import get_redis


class TaskCancelled(Exception):
    """Raised inside a task when the user asked to cancel it."""


def cancel_key(task_id: str) -> str:
    return f"task:cancel:{task_id}"


class ProgressReporter:
    """Writes progress to the ``task_run`` row (throttled) and checks the cancel flag."""

    def __init__(self, task_id: str, throttle_seconds: float = 1.0):
        self.task_id = task_id
        self.throttle = throttle_seconds
        self._last_write = 0.0
        self.current = 0
        self.total: int | None = None
        self.message: str | None = None

    def update(
        self,
        current: int | None = None,
        total: int | None = None,
        message: str | None = None,
        *,
        advance: int = 0,
        force: bool = False,
    ) -> None:
        if current is not None:
            self.current = current
        self.current += advance
        if total is not None:
            self.total = total
        if message is not None:
            self.message = message
        now = time.monotonic()
        if force or now - self._last_write >= self.throttle:
            self._last_write = now
            self._write()

    def phase(self, message: str, total: int | None = None) -> None:
        """Start a new phase: resets the counter and writes immediately."""
        self.current = 0
        self.total = total
        self.message = message
        self._last_write = time.monotonic()
        self._write()
        self.check_cancelled()

    def _write(self) -> None:
        with get_engine().begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE task_run
                    SET status = CASE WHEN status IN ('PENDING', 'STARTED') THEN 'PROGRESS' ELSE status END,
                        progress_current = :c, progress_total = :t, message = :m
                    WHERE id = :id
                    """
                ),
                {"c": self.current, "t": self.total, "m": self.message, "id": self.task_id},
            )

    def is_cancelled(self) -> bool:
        try:
            return bool(get_redis().exists(cancel_key(self.task_id)))
        except Exception:
            return False

    def check_cancelled(self) -> None:
        if self.is_cancelled():
            raise TaskCancelled()
