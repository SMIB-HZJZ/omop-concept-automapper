"""Helper for endpoints that start background tasks."""

from fastapi import HTTPException, status

from app.models import AppUser
from app.schemas.api import TaskRef
from app.services import tasks as task_service


def start_task(session, kind: str, celery_task, params: dict, user: AppUser) -> TaskRef:
    try:
        run = task_service.launch(session, kind, celery_task, params, user)
    except task_service.TaskConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except task_service.TaskLaunchError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    return TaskRef(task_id=run.id, kind=run.kind)
