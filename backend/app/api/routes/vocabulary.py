from fastapi import APIRouter, status

from app.api.deps import AdminUser, CurrentUser, SessionDep
from app.api.routes._tasks import start_task
from app.core.config import get_settings
from app.schemas.api import Atc7Stats, TaskRef, VocabularyStatus
from app.services.atc7 import get_atc7_statistics
from app.services.vocabulary_status import check_vocabulary_files, get_table_status
from app.worker.tasks.atc7 import process_atc7
from app.worker.tasks.vocabulary import import_vocabulary

router = APIRouter(tags=["vocabulary"])


@router.get("/vocabulary/status", response_model=VocabularyStatus)
def vocabulary_status(_: CurrentUser, session: SessionDep):
    files = check_vocabulary_files()
    return VocabularyStatus(
        tables=get_table_status(session),
        files=[{"table_name": t, **f} for t, f in files.items()],  # extra keys are dropped
        vocabulary_dir=get_settings().vocabulary_dir,
    )


@router.post("/vocabulary/import", response_model=TaskRef, status_code=status.HTTP_202_ACCEPTED)
def start_vocabulary_import(user: AdminUser, session: SessionDep):
    return start_task(session, "vocabulary.import", import_vocabulary, {}, user)


@router.get("/atc7/stats", response_model=Atc7Stats)
def atc7_stats(_: CurrentUser, session: SessionDep):
    return get_atc7_statistics(session)


@router.post("/atc7/process", response_model=TaskRef, status_code=status.HTTP_202_ACCEPTED)
def start_atc7(user: AdminUser, session: SessionDep):
    return start_task(session, "atc7.process", process_atc7, {}, user)
