"""Presence (who has which term open), team activity and the OMOP SOURCE_TO_CONCEPT_MAP export."""

import re

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import StreamingResponse

from app.api.deps import CurrentUser, SessionDep
from app.models import SourceVocabulary
from app.schemas.api import Message, Presence, TeamActivity
from app.services import collaboration, mapping, releases

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
    _: CurrentUser,
    session: SessionDep,
    source_vocabulary_id: str | None = Query(
        None, max_length=20, description="SOURCE_VOCABULARY_ID to write (default: from the name)"
    ),
    release: str | None = Query(
        None,
        pattern=r"^([1-9][0-9]{0,5}|draft)$",
        description="Release number, or 'draft' for the current mappings as the next release "
        "(default: the latest release; the current mappings when there is none)",
    ),
):
    vocab = _vocabulary(session, vocabulary_id)
    which: int | str | None = int(release) if release and release.isdigit() else release
    if isinstance(which, int) and not releases.release_exists(session, vocabulary_id, which):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Release {which} not found")
    # OMOP vocabulary_id is at most 20 characters; derive one from the name when not given
    label = source_vocabulary_id or releases.default_label(vocab.name, vocabulary_id)
    return StreamingResponse(
        releases.iter_source_to_concept_map(vocabulary_id, label, which),
        media_type="text/csv",
        headers={
            "Content-Disposition": "attachment; "
            f'filename="source_to_concept_map_{vocabulary_id}{"_" + release if release else ""}.csv"'
        },
    )


_PART = re.compile(r"^([1-9][0-9]{0,8})(?::([1-9][0-9]{0,5})?)?(?::([A-Za-z0-9_-]{1,20}))?$")


@router.get("/source_to_concept_map.csv", response_class=StreamingResponse)
def combined_source_to_concept_map(
    _: CurrentUser,
    session: SessionDep,
    vocabulary: list[str] = Query(
        ...,
        description="One per source vocabulary: 'id', 'id:release' or 'id:release:LABEL' "
        "('id::LABEL' for the latest release). LABEL is the SOURCE_VOCABULARY_ID written "
        "(default: from the name). Only published releases are exported.",
    ),
):
    """The OMOP SOURCE_TO_CONCEPT_MAP of several published vocabularies in one file."""
    parts: list[tuple[int, str, int]] = []
    for item in vocabulary:
        match = _PART.match(item.strip())
        if match is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid vocabulary item {item!r}")
        vocabulary_id = int(match[1])
        vocab = _vocabulary(session, vocabulary_id)
        latest = releases.latest_release(session, vocabulary_id)
        if latest is None:
            raise HTTPException(
                status.HTTP_409_CONFLICT, f"{vocab.name} has no published release to export"
            )
        number = int(match[2]) if match[2] else latest
        if number != latest and not releases.release_exists(session, vocabulary_id, number):
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, f"Release {number} of {vocab.name} not found"
            )
        parts.append(
            (vocabulary_id, match[3] or releases.default_label(vocab.name, vocabulary_id), number)
        )
    ids = [p[0] for p in parts]
    if len(set(ids)) != len(ids):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A vocabulary is listed twice")
    labels = [p[1] for p in parts]
    if len(set(labels)) != len(labels):
        # Codes of two vocabularies would be indistinguishable in one table
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Each vocabulary needs its own SOURCE_VOCABULARY_ID: "
            + ", ".join(sorted({x for x in labels if labels.count(x) > 1})),
        )
    return StreamingResponse(
        releases.iter_combined_source_to_concept_map(parts),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="source_to_concept_map.csv"'},
    )
