"""Presence (who has which term open), team activity and the OMOP SOURCE_TO_CONCEPT_MAP export."""

import re

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import StreamingResponse

from app.api.deps import AdminUser, CurrentUser, SessionDep
from app.models import SourceVocabulary
from app.schemas.api import Message, Presence, TeamActivity
from app.services import collaboration, mapping

router = APIRouter(tags=["collaboration"])


def _vocabulary(session, vocabulary_id: int) -> SourceVocabulary:
    vocab = session.get(SourceVocabulary, vocabulary_id)
    if vocab is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Source vocabulary {vocabulary_id} not found"
        )
    return vocab


@router.put("/source-concepts/{source_id}/presence", response_model=list[Presence])
def open_term(source_id: int, user: CurrentUser, session: SessionDep):
    """Called every ~20 s while a user has the term open; returns everyone in the vocabulary."""
    vocabularies = mapping.vocabulary_of_sources(session, [source_id])
    if not vocabularies:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Source concept {source_id} not found")
    vocabulary_id = next(iter(vocabularies))
    collaboration.touch(vocabulary_id, source_id, user.id, user.username)
    return collaboration.who(vocabulary_id)


@router.delete("/source-vocabularies/{vocabulary_id}/presence", response_model=Message)
def close_term(vocabulary_id: int, user: CurrentUser):
    collaboration.leave(vocabulary_id, user.id)
    return Message(message="Left")


@router.get("/source-vocabularies/{vocabulary_id}/presence", response_model=list[Presence])
def presence(vocabulary_id: int, _: CurrentUser):
    return collaboration.who(vocabulary_id)


@router.get("/source-vocabularies/{vocabulary_id}/activity", response_model=list[TeamActivity])
def team_activity(vocabulary_id: int, _: CurrentUser, session: SessionDep):
    _vocabulary(session, vocabulary_id)
    return collaboration.activity(session, vocabulary_id)


@router.get(
    "/source-vocabularies/{vocabulary_id}/source_to_concept_map.csv",
    response_class=StreamingResponse,
)
def source_to_concept_map(
    vocabulary_id: int,
    _: AdminUser,
    session: SessionDep,
    source_vocabulary_id: str | None = Query(
        None, max_length=20, description="SOURCE_VOCABULARY_ID to write (default: from the name)"
    ),
):
    vocab = _vocabulary(session, vocabulary_id)
    # OMOP vocabulary_id is at most 20 characters; derive one from the name when not given
    label = source_vocabulary_id or re.sub(r"[^A-Za-z0-9_-]+", "_", vocab.name).strip("_")[:20]
    return StreamingResponse(
        collaboration.iter_source_to_concept_map(vocabulary_id, label or f"SOURCE_{vocabulary_id}"),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="source_to_concept_map_{vocabulary_id}.csv"'
        },
    )
