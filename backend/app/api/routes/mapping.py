import asyncio

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import CurrentUser, SessionDep
from app.api.routes._tasks import start_task
from app.core.db import session_scope
from app.models import SourceVocabulary
from app.schemas.api import (
    AutomapRequest,
    Calibration,
    CountResult,
    MapRequest,
    Message,
    SourceFlag,
    SuggestionDecision,
    SuggestionPage,
    SuggestRequest,
    SuggestResponse,
    TaskRef,
    UnmapRequest,
)
from app.services import mapping, suggestions
from app.services.atc7 import is_valid_atc7
from app.services.settings_store import EffectiveConfig, get_effective_config
from app.vector.store import CollectionMissing
from app.worker.tasks.automap import run_automap
from app.workflows.builder import PipelineError, get_pipeline
from app.workflows.service import build_context, default_pipeline, initial_state, run_pipeline

router = APIRouter(tags=["mapping"])


@router.post("/mappings", response_model=CountResult)
def map_concepts(body: MapRequest, user: CurrentUser, session: SessionDep):
    source = mapping.get_source_concept(session, body.source_id)
    if source is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Source concept {body.source_id} not found")
    found = mapping.existing_concept_ids(session, body.concept_ids)
    missing = [c for c in body.concept_ids if c not in found]
    if missing:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Concept ID(s) not found: {missing}")
    mapping.map_concepts(
        session, body.source_id, body.concept_ids, method="manual", user_id=user.id
    )
    mapping.invalidate_vocabulary(source["source_vocabulary_id"])
    return CountResult(count=len(set(body.concept_ids)))


@router.post("/mappings/unmap", response_model=CountResult)
def unmap(body: UnmapRequest, _: CurrentUser, session: SessionDep):
    vocabularies = mapping.vocabulary_of_sources(session, body.source_ids)
    deleted = mapping.unmap(session, body.source_ids)
    for vocabulary_id in vocabularies:
        mapping.invalidate_vocabulary(vocabulary_id)
    return CountResult(count=deleted)


@router.post(
    "/source-vocabularies/{vocabulary_id}/automap",
    response_model=TaskRef,
    status_code=status.HTTP_202_ACCEPTED,
)
def automap(vocabulary_id: int, body: AutomapRequest, user: CurrentUser, session: SessionDep):
    if session.get(SourceVocabulary, vocabulary_id) is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Source vocabulary {vocabulary_id} not found"
        )
    try:
        get_pipeline(body.pipeline)
    except PipelineError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    params = {"vocabulary_id": vocabulary_id, **body.model_dump()}
    params["pipeline"] = body.pipeline or default_pipeline(body.drug_specific)
    return start_task(session, "automap.run", run_automap, params, user)


def _load_for_suggest(source_id: int) -> tuple[dict | None, EffectiveConfig]:
    with session_scope() as session:
        return mapping.get_source_concept(session, source_id), get_effective_config(session)


@router.put("/source-concepts/{source_id}/flag", response_model=Message)
def flag_source(source_id: int, body: SourceFlag, user: CurrentUser, session: SessionDep):
    if not mapping.set_flag(session, source_id, body.flag, body.note, user.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Source concept {source_id} not found")
    return Message(message="Flag cleared" if body.flag is None else f"Flagged as {body.flag}")


@router.post("/source-concepts/{source_id}/suggest", response_model=SuggestResponse)
async def suggest(source_id: int, body: SuggestRequest, _: CurrentUser):
    atc7 = [c.strip().upper() for c in (body.atc7_codes or []) if c.strip()]
    if any(not is_valid_atc7(c) for c in atc7):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid ATC7 code")
    # Blocking DB work runs in a thread; the event loop only awaits the (slow) LLM pipeline
    source, cfg = await asyncio.to_thread(_load_for_suggest, source_id)
    if source is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Source concept {source_id} not found")
    state = initial_state(
        source_id=source_id,
        source_value=source["source_value"],
        source_name=source["source_concept_name"],
        query=body.query,
        domains=body.domains,
        drug_specific=source["is_drug"] if body.drug_specific is None else body.drug_specific,
        threshold=body.threshold,
        atc7_codes=atc7 or None,
        drug_structure=source.get("drug_structure"),
        domain_mode=body.domain_mode,
        code_vocabularies=source.get("code_vocabularies") or [],
    )
    if body.query:
        # An edited search name is both the search query and the term the LLM judges
        state["source_name"] = body.query
    try:
        pipeline = body.pipeline or await asyncio.to_thread(
            default_pipeline, bool(state["drug_specific"])
        )
        compiled = await asyncio.to_thread(get_pipeline, pipeline)
        result = await run_pipeline(state, build_context(cfg), compiled=compiled)
    except PipelineError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except CollectionMissing as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    selected = result.get("selected")
    return SuggestResponse(
        source_id=source_id,
        query=result.get("query") or state["query"],
        pipeline=result["meta"]["pipeline"],
        outcome=result.get("outcome", "no_match"),
        selected=({"rank": 0, "score": 0.0, **selected} if selected else None),
        selected_concepts=[
            {"rank": 0, "score": 0.0, **c} for c in result.get("selected_concepts") or []
        ],
        confidence=result.get("confidence"),
        rationale=result.get("rationale"),
        match_type=result.get("match_type"),
        candidates=result.get("candidates") or [],
        trace=result.get("trace") or [],
    )


@router.get("/suggestions/calibration", response_model=Calibration)
def calibration(
    _: CurrentUser,
    session: SessionDep,
    vocabulary_id: int | None = Query(None),
    target_precision: float = Query(0.95, gt=0, le=1),
):
    """How often reviewers accepted suggestions per confidence level; the recommended threshold
    is the lowest confidence that keeps precision at or above the target."""
    return suggestions.calibration(session, vocabulary_id, target_precision)


@router.get("/source-vocabularies/{vocabulary_id}/suggestions", response_model=SuggestionPage)
def list_suggestions(
    vocabulary_id: int,
    _: CurrentUser,
    session: SessionDep,
    status_: str = Query("pending", alias="status", pattern="^(pending|accepted|rejected)$"),
    min_confidence: int | None = Query(None, ge=1, le=10),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=500),
    sort: str = Query("confidence_desc"),
    source_id: int | None = Query(None, description="Only this source term's suggestions"),
):
    return suggestions.list_suggestions(
        session,
        vocabulary_id,
        status=status_,
        min_confidence=min_confidence,
        page=page,
        per_page=per_page,
        sort=sort,
        source_id=source_id,
    )


def _decision_args(body: SuggestionDecision) -> dict:
    if not body.ids and body.vocabulary_id is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Provide suggestion ids or a vocabulary_id"
        )
    return {
        "ids": body.ids,
        "vocabulary_id": body.vocabulary_id,
        "min_confidence": body.min_confidence,
    }


@router.post("/suggestions/accept", response_model=CountResult)
def accept_suggestions(body: SuggestionDecision, user: CurrentUser, session: SessionDep):
    return CountResult(count=suggestions.accept(session, user_id=user.id, **_decision_args(body)))


@router.post("/suggestions/reject", response_model=CountResult)
def reject_suggestions(body: SuggestionDecision, user: CurrentUser, session: SessionDep):
    return CountResult(count=suggestions.reject(session, user_id=user.id, **_decision_args(body)))
