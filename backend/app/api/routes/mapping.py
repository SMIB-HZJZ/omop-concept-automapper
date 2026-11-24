import asyncio

from fastapi import APIRouter, HTTPException, Path, Query, status

from app.api.deps import AdminUser, CurrentUser, SessionDep
from app.api.routes._tasks import start_task
from app.core.db import session_scope
from app.core.redis import get_redis
from app.llm.prompts import vocabulary_context
from app.models import SourceVocabulary
from app.schemas.api import (
    AutomapRequest,
    Calibration,
    CountResult,
    DrugMatchRequest,
    DrugMatchResponse,
    MapRequest,
    Message,
    SourceFlag,
    SuggestionDecision,
    SuggestionPage,
    SuggestProgress,
    SuggestRequest,
    SuggestResponse,
    TaskRef,
    UnmapRequest,
)
from app.services import mapping, suggestions
from app.services.atc7 import is_valid_atc7
from app.services.releases import LOCKED_MESSAGE
from app.services.settings_store import EffectiveConfig, get_effective_config
from app.vector.store import CollectionMissing
from app.worker.tasks.automap import run_automap
from app.workflows.builder import PipelineError, get_pipeline
from app.workflows.nodes.drug import MatchDrugParams, match_drug
from app.workflows.nodes.retrieve import excluded_drug_classes
from app.workflows.service import (
    build_context,
    build_drug_context,
    default_pipeline,
    initial_state,
    run_pipeline,
)
from app.workflows.steps import decision_path, step_label

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
def unmap(body: UnmapRequest, user: CurrentUser, session: SessionDep):
    # Users may undo their own mappings (e.g. "Undo" after Confirm); bulk unmapping is for admins
    if user.role != "admin" and not mapping.all_mapped_by(session, body.source_ids, user.id):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Only admins can remove mappings made by someone else"
        )
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
def automap(vocabulary_id: int, body: AutomapRequest, user: AdminUser, session: SessionDep):
    vocab = session.get(SourceVocabulary, vocabulary_id)
    if vocab is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Source vocabulary {vocabulary_id} not found"
        )
    if vocab.locked:
        raise HTTPException(status.HTTP_409_CONFLICT, LOCKED_MESSAGE.format(name=vocab.name))
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


_PROGRESS_ID = "^[0-9a-f-]{8,64}$"
_PROGRESS_TTL = 300


def _progress_key(progress_id: str) -> str:
    return f"suggest-step:{progress_id}"


@router.get("/suggest-progress/{progress_id}", response_model=SuggestProgress)
def suggest_progress(_: CurrentUser, progress_id: str = Path(pattern=_PROGRESS_ID)):
    """The step an AI suggest run (started with this ``progress`` id) is at, while it runs."""
    try:
        return SuggestProgress(step=get_redis().get(_progress_key(progress_id)))
    except Exception:
        return SuggestProgress(step=None)


@router.post("/source-concepts/{source_id}/suggest", response_model=SuggestResponse)
async def suggest(
    source_id: int,
    body: SuggestRequest,
    _: CurrentUser,
    progress: str | None = Query(
        None, pattern=_PROGRESS_ID, description="Id under which the current step can be polled"
    ),
):
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
        vocabulary_context=vocabulary_context(
            source.get("vocabulary_name"), source.get("vocabulary_description")
        ),
        source_language=source.get("source_language"),
    )
    if body.query:
        # An edited search name is both the search query and the term the LLM judges
        state["source_name"] = body.query
    try:
        pipeline = body.pipeline or await asyncio.to_thread(
            default_pipeline, bool(state["drug_specific"])
        )
        compiled = await asyncio.to_thread(get_pipeline, pipeline)
        ctx = await asyncio.to_thread(build_context, cfg)
        if progress:
            key = _progress_key(progress)

            def on_step(_state, node_id, uses, detail):
                label = step_label(node_id, uses, detail)
                if label:
                    get_redis().set(key, label, ex=_PROGRESS_TTL)  # a local Redis: ~1 ms

            ctx.on_step = on_step
        result = await run_pipeline(state, ctx, compiled=compiled)
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
        decision_path=decision_path(result),
        candidates=result.get("candidates") or [],
        trace=result.get("trace") or [],
    )


def _drug_step_params() -> MatchDrugParams:
    """The drug step's settings in the default drug pipeline, so the panel decides like
    auto-mapping (e.g. whether branded drugs are left out)."""
    _graph, definition = get_pipeline(default_pipeline(True))
    for ref in definition.nodes.values():
        if ref.uses == "match_drug":
            return MatchDrugParams.model_validate(ref.params)
    return MatchDrugParams()


@router.post("/source-concepts/{source_id}/drug-match", response_model=DrugMatchResponse)
async def drug_match(source_id: int, body: DrugMatchRequest, _: CurrentUser):
    """Run auto-mapping's deterministic drug step for one term: its structure (provided, edited
    or parsed from the name by the LLM) matched on drug_strength, with the matching drugs."""
    source, cfg = await asyncio.to_thread(_load_for_suggest, source_id)
    if source is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Source concept {source_id} not found")
    edited = body.structure is not None
    state = initial_state(
        source_id=source_id,
        source_value=source["source_value"],
        source_name=source["source_concept_name"],
        domains=["Drug"],
        drug_specific=True,
        threshold=10,
        drug_structure=(
            body.structure.model_dump(exclude_none=True) if edited else source.get("drug_structure")
        ),
    )
    # build_context reads the database and Redis: off the event loop
    ctx, params = await asyncio.to_thread(lambda: (build_drug_context(cfg), _drug_step_params()))
    if ctx.drug_matcher is None:
        return DrugMatchResponse(source_id=source_id, status="unavailable")
    # An edited structure is matched as it is, not replaced by a parse of the name
    params = params.model_copy(update={"parse_free_text": params.parse_free_text and not edited})
    _route, update = await match_drug(state, ctx, params)
    meta = update.get("meta") or {}
    concepts = update.get("selected_concepts") or (
        [update["selected"]] if update.get("selected") else update.get("structured_candidates")
    )
    origin = meta.get("drug_origin")
    return DrugMatchResponse(
        source_id=source_id,
        status=meta.get("drug_status", "none"),
        origin="edited" if edited and origin == "provided" else origin,
        structure=meta.get("drug_structure"),
        level=meta.get("drug_level"),
        reason=meta.get("drug_match"),
        summary=update.get("_summary"),
        resolved=meta.get("drug_match_resolved") or [],
        approximate=meta.get("drug_match_approximate") or [],
        search_text=meta.get("drug_search_text"),
        brand_in_name=meta.get("drug_brand_in_name"),
        exclude_classes=list(excluded_drug_classes({**state, "meta": meta})),
        candidates=[{"rank": i + 1, "score": 1.0, **c} for i, c in enumerate(concepts or [])],
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
    status_: str = Query(
        "pending", alias="status", pattern="^(pending|accepted|rejected|below_threshold)$"
    ),
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
