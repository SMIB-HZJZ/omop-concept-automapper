"""Mapping releases: publish (and lock) a source vocabulary's mappings, start the next release."""

from fastapi import APIRouter, HTTPException, status

from app.api.deps import AdminUser, CurrentUser, SessionDep
from app.models import SourceVocabulary
from app.schemas.api import ReleaseCreate, ReleaseState
from app.services import releases

router = APIRouter(prefix="/source-vocabularies/{vocabulary_id}/releases", tags=["releases"])


def _check(session, vocabulary_id: int) -> None:
    if session.get(SourceVocabulary, vocabulary_id) is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Source vocabulary {vocabulary_id} not found"
        )


@router.get("", response_model=ReleaseState)
def release_state(vocabulary_id: int, _: CurrentUser, session: SessionDep):
    _check(session, vocabulary_id)
    return releases.release_state(session, vocabulary_id)


@router.post("", response_model=ReleaseState, status_code=status.HTTP_201_CREATED)
def publish(vocabulary_id: int, body: ReleaseCreate, user: AdminUser, session: SessionDep):
    """Publish the current mappings as the next release and lock the vocabulary."""
    _check(session, vocabulary_id)
    note = " ".join((body.note or "").split()) or None
    try:
        releases.publish(session, vocabulary_id, user.id, note)
    except releases.ReleaseError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return releases.release_state(session, vocabulary_id)


@router.post("/next", response_model=ReleaseState)
def start_next(vocabulary_id: int, _: AdminUser, session: SessionDep):
    """Unlock a released vocabulary; changes from now on go into the next release."""
    _check(session, vocabulary_id)
    try:
        releases.start_next(session, vocabulary_id)
    except releases.ReleaseError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return releases.release_state(session, vocabulary_id)
