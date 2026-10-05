from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import CurrentUser, SessionDep
from app.schemas.api import TaskOut
from app.services import tasks as task_service

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("", response_model=list[TaskOut])
def list_tasks(
    user: CurrentUser,
    session: SessionDep,
    active: bool | None = Query(None),
    kind: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
):
    # Users see their own tasks; admins see everything
    owner = None if user.role == "admin" else user.id
    return task_service.list_tasks(session, active=active, kind=kind, user_id=owner, limit=limit)


def _visible(task: dict | None, user) -> dict:
    if task is None or (user.role != "admin" and task["user_id"] != user.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task not found")
    return task


@router.get("/{task_id}", response_model=TaskOut)
def get_task(task_id: str, user: CurrentUser, session: SessionDep):
    return _visible(task_service.get_task(session, task_id), user)


@router.post("/{task_id}/cancel", response_model=TaskOut)
def cancel_task(task_id: str, user: CurrentUser, session: SessionDep):
    _visible(task_service.get_task(session, task_id), user)
    return task_service.cancel(session, task_id)
