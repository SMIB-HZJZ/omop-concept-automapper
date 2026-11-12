"""Launching background tasks and reading their state."""

import uuid
from typing import Any

from fastapi.encoders import jsonable_encoder
from sqlalchemy import text
from sqlmodel import Session, select

from app.core.db import get_engine
from app.core.redis import get_redis
from app.models import AppUser, TaskRun
from app.schemas.task_results import check_result
from app.worker.progress import cancel_key, heartbeat_key

ACTIVE_STATUSES = ("PENDING", "STARTED", "PROGRESS")
TERMINAL_STATUSES = ("SUCCESS", "FAILURE", "CANCELLED")

# Kinds that must never run concurrently with another task of the same group
LOCK_GROUPS = {
    "vocabulary.import": "vocab",
    "atc7.process": "vocab",
    "embeddings.embed": "embed",
    "embeddings.sync_metadata": "embed",
    "embeddings.sync_atc7": "embed",
}


class TaskConflict(Exception):
    pass


class TaskLaunchError(Exception):
    """The task could not be queued (broker unreachable)."""


def running_in_group(session: Session, group: str) -> str | None:
    """Kind of an active task in ``group``, if any (stale rows of dead workers are ignored)."""
    fail_orphaned_tasks()
    kinds = [k for k, g in LOCK_GROUPS.items() if g == group]
    return session.execute(
        text(
            "SELECT kind FROM task_run WHERE kind = ANY(:kinds) AND status = ANY(:active) LIMIT 1"
        ),
        {"kinds": kinds, "active": list(ACTIVE_STATUSES)},
    ).scalar()


def launch(
    session: Session,
    kind: str,
    celery_task: Any,
    params: dict[str, Any],
    user: AppUser | None,
) -> TaskRun:
    group = LOCK_GROUPS.get(kind)
    if group:
        # Serialise launches per group so two requests cannot both pass the check below
        session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": f"task-group:{group}"}
        )
        running = running_in_group(session, group)
        if running:
            session.rollback()
            raise TaskConflict(
                f"A '{running}' task is already running; wait for it to finish or cancel it"
            )

    task_id = str(uuid.uuid4())
    run = TaskRun(
        id=task_id,
        kind=kind,
        status="PENDING",
        params=jsonable_encoder(params),
        user_id=user.id if user else None,
    )
    session.add(run)
    session.commit()
    try:
        celery_task.apply_async(
            kwargs={**params, "user_id": user.id if user else None}, task_id=task_id
        )
    except Exception as exc:
        # Otherwise the row would stay PENDING forever and block its group
        session.execute(
            text(
                "UPDATE task_run SET status = 'FAILURE', error = :e, finished_at = now() "
                "WHERE id = :id AND status = 'PENDING'"
            ),
            {"id": task_id, "e": f"Could not queue the task: {exc}"},
        )
        session.commit()
        raise TaskLaunchError(f"Could not queue the task: {exc}") from exc
    session.refresh(run)
    return run


def to_dict(run: TaskRun, username: str | None = None) -> dict:
    check_result(run.kind, run.result)
    data = run.model_dump()
    total = run.progress_total
    data["percentage"] = (
        100.0
        if run.status == "SUCCESS"
        else (run.progress_current / total * 100 if total else None)
    )
    data["is_terminal"] = run.status in TERMINAL_STATUSES
    data["username"] = username
    return data


def list_tasks(
    session: Session,
    *,
    active: bool | None = None,
    kind: str | None = None,
    user_id: int | None = None,
    limit: int = 50,
) -> list[dict]:
    clauses, params = [], {"limit": limit}
    if active is True:
        clauses.append("t.status = ANY(:active)")
        params["active"] = list(ACTIVE_STATUSES)
    elif active is False:
        clauses.append("t.status = ANY(:terminal)")
        params["terminal"] = list(TERMINAL_STATUSES)
    if kind:
        clauses.append("t.kind = :kind")
        params["kind"] = kind
    if user_id is not None:
        clauses.append("t.user_id = :uid")
        params["uid"] = user_id
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = session.execute(
        select(TaskRun, AppUser.username)
        .from_statement(
            text(
                f"SELECT t.*, u.username FROM task_run t LEFT JOIN app_user u ON u.id = t.user_id "
                f"{where} ORDER BY t.created_at DESC LIMIT :limit"
            )
        )
        .params(params)
    ).all()
    return [to_dict(run, username) for run, username in rows]


def get_task(session: Session, task_id: str) -> dict | None:
    session.expire_all()
    run = session.get(TaskRun, task_id)
    if run is None:
        return None
    username = None
    if run.user_id:
        user = session.get(AppUser, run.user_id)
        username = user.username if user else None
    return to_dict(run, username)


def cancel(session: Session, task_id: str) -> dict | None:
    run = session.get(TaskRun, task_id)
    if run is None:
        return None
    if run.status in TERMINAL_STATUSES:
        return to_dict(run)
    get_redis().set(cancel_key(task_id), "1", ex=24 * 3600)
    if run.status == "PENDING":
        # Not started yet: drop it from the queue and close the record right away
        from app.worker.celery_app import celery_app

        celery_app.control.revoke(task_id)
        session.execute(
            text(
                "UPDATE task_run SET status = 'CANCELLED', message = 'Cancelled before start', "
                "finished_at = now() WHERE id = :id AND status = 'PENDING'"
            ),
            {"id": task_id},
        )
        session.commit()
    else:
        run.message = "Cancelling…"
        session.add(run)
        session.commit()
    session.refresh(run)
    return to_dict(run)


def fail_orphaned_tasks() -> int:
    """Mark running tasks whose worker stopped sending heartbeats as failed.

    Safe with several workers: a task is orphaned only when its own heartbeat has expired."""
    with get_engine().begin() as conn:
        running = (
            conn.execute(text("SELECT id FROM task_run WHERE status IN ('STARTED', 'PROGRESS')"))
            .scalars()
            .all()
        )
        if not running:
            return 0
        redis = get_redis()
        alive = redis.mget([heartbeat_key(t) for t in running])
        dead = [t for t, beat in zip(running, alive, strict=True) if beat is None]
        if not dead:
            return 0
        return conn.execute(
            text(
                "UPDATE task_run SET status = 'FAILURE', "
                "error = 'The worker stopped while the task was running', finished_at = now() "
                "WHERE id = ANY(:ids) AND status IN ('STARTED', 'PROGRESS')"
            ),
            {"ids": dead},
        ).rowcount
