import os
import shutil
import uuid

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.concurrency import run_in_threadpool

from app.api.deps import AdminUser, CurrentUser, SessionDep
from app.api.routes._tasks import start_task
from app.core.config import get_settings
from app.schemas.api import Atc7Stats, TaskRef, VocabularyStatus
from app.services.atc7 import get_atc7_statistics
from app.services.vocabulary_status import (
    ArchiveError,
    archive_files,
    get_table_status,
    get_vocabulary_version,
)
from app.worker.tasks.vocabulary import import_vocabulary

router = APIRouter(tags=["vocabulary"])


@router.get("/vocabulary/status", response_model=VocabularyStatus)
def vocabulary_status(_: CurrentUser, session: SessionDep):
    return VocabularyStatus(
        tables=get_table_status(session),
        vocabulary_version=get_vocabulary_version(session),
        max_upload_bytes=get_settings().vocabulary_upload_max_mb * 1024 * 1024,
    )


@router.post(
    "/vocabulary/import",
    response_model=TaskRef,
    status_code=status.HTTP_202_ACCEPTED,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/zip": {"schema": {"type": "string", "format": "binary"}}},
        }
    },
)
async def start_vocabulary_import(
    request: Request,
    user: AdminUser,
    session: SessionDep,
    filename: str | None = Query(None, max_length=255, description="Name of the uploaded zip"),
):
    """Upload an Athena vocabulary download (the zip as it is) and import it in the background.

    The zip is the request body (not a form), so a multi-GB file is streamed straight to disk."""
    limit = get_settings().vocabulary_upload_max_mb * 1024 * 1024
    declared = int(request.headers.get("content-length") or 0)
    if declared > limit:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, _too_large(limit))
    upload_dir = get_settings().upload_dir
    os.makedirs(upload_dir, exist_ok=True)
    if declared and shutil.disk_usage(upload_dir).free < declared * 1.1:
        raise HTTPException(
            status.HTTP_507_INSUFFICIENT_STORAGE, "Not enough disk space for the upload"
        )
    path = os.path.join(upload_dir, f"vocabulary-{uuid.uuid4()}.zip")
    try:
        written = 0
        with open(path, "wb") as out:
            async for chunk in request.stream():
                written += len(chunk)
                if written > limit:
                    raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, _too_large(limit))
                await run_in_threadpool(out.write, chunk)
        if not written:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "The upload is empty")
        try:
            await run_in_threadpool(archive_files, path)
        except ArchiveError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
        return await run_in_threadpool(
            start_task,
            session,
            "vocabulary.import",
            import_vocabulary,
            {"path": path, "filename": filename},
            user,
        )
    except BaseException:
        # The worker owns (and deletes) the file only once the task has been queued
        if os.path.exists(path):
            os.remove(path)
        raise


def _too_large(limit: int) -> str:
    return f"The zip is larger than {limit // (1024 * 1024):,} MB (VOCABULARY_UPLOAD_MAX_MB)"


@router.get("/atc7/stats", response_model=Atc7Stats)
def atc7_stats(_: CurrentUser, session: SessionDep):
    return get_atc7_statistics(session)
