import asyncio
import logging
import re

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import CurrentUser, SessionDep
from app.core.config import get_settings
from app.llm.embeddings import Embedder
from app.llm.explain import explain_concept
from app.schemas.api import (
    ConceptDetails,
    ConceptExplanation,
    ConceptFacet,
    ConceptLookup,
    ConceptOut,
    SearchRequest,
    SearchResponse,
)
from app.services import concepts as concept_service
from app.services.settings_store import get_effective_config
from app.vector.store import (
    CollectionMissing,
    PgVectorStore,
    SearchFilters,
    collection_exists,
    get_collection,
)

router = APIRouter(tags=["concepts"])
logger = logging.getLogger(__name__)


@router.get("/concepts", response_model=ConceptLookup)
def lookup_concepts(
    _: CurrentUser,
    session: SessionDep,
    ids: str = Query(..., description="Concept ids separated by ';' or ','"),
):
    parts = [p.strip() for p in re.split(r"[;,]", ids) if p.strip()]
    bad = [p for p in parts if not p.isdigit()]
    if bad:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Please enter valid concept IDs separated by semicolons: {bad}",
        )
    wanted = list(dict.fromkeys(int(p) for p in parts))
    found = concept_service.get_concepts(session, wanted)
    found_ids = {c["concept_id"] for c in found}
    return ConceptLookup(found=found, missing=[i for i in wanted if i not in found_ids])


@router.get("/concept-facets", response_model=list[ConceptFacet])
def concept_facets(_: CurrentUser, session: SessionDep):
    """Domain / vocabulary / concept class combinations of the concepts search can find, for the
    search filters."""
    cfg = get_effective_config(session)
    collection = get_collection(session, cfg.collection_name)
    table = (
        collection.table_name
        if collection and collection_exists(session, cfg.collection_name)
        else None
    )
    return concept_service.concept_facets(session, table)


@router.get("/concepts/{concept_id}", response_model=ConceptOut)
def get_concept(concept_id: int, _: CurrentUser, session: SessionDep):
    found = concept_service.get_concepts(session, [concept_id])
    if not found:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Concept ID {concept_id} not found")
    return found[0]


@router.get("/concepts/{concept_id}/details", response_model=ConceptDetails)
def get_concept_details(concept_id: int, _: CurrentUser, session: SessionDep):
    details = concept_service.get_details(session, concept_id)
    if details is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Concept ID {concept_id} not found")
    return details


@router.post("/concepts/{concept_id}/explain", response_model=ConceptExplanation)
async def explain(concept_id: int, _: CurrentUser, session: SessionDep):
    details = await asyncio.to_thread(concept_service.get_details, session, concept_id)
    if details is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Concept ID {concept_id} not found")
    # A separate, cheaper model (LLM_EXPLAIN_MODEL); empty = the configured chat model
    model = get_settings().llm_explain_model.strip()
    if not model:
        model = (await asyncio.to_thread(get_effective_config, session)).llm_model
    try:
        text_, cached = await explain_concept(details, model)
    except Exception as exc:
        logger.warning("Explaining concept %s failed: %s", concept_id, exc)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"The AI explanation failed: {type(exc).__name__}"
        ) from exc
    return ConceptExplanation(concept_id=concept_id, explanation=text_, model=model, cached=cached)


@router.post("/search", response_model=SearchResponse)
def search(body: SearchRequest, _: CurrentUser, session: SessionDep):
    cfg = get_effective_config(session)
    vector_store = PgVectorStore(
        session,
        cfg.collection_name,
        Embedder(cfg.embedding_model, cfg.embedding_dims, cfg.embedding_task_types),
    )
    filters = SearchFilters(
        domains=body.domains,
        vocabulary_id=body.vocabulary_id or None,
        vocabularies=body.vocabularies,
        concept_classes=body.concept_classes,
        exclude_concept_classes=body.exclude_concept_classes,
        atc7_codes=body.atc7_codes,
    )
    queries = list(dict.fromkeys(q.strip() for q in [body.query, *body.extra_queries] if q.strip()))

    def run(f: SearchFilters) -> list[dict]:
        # Several queries: the best score per concept, as auto-mapping merges its searches
        best: dict[int, dict] = {}
        for q in queries:
            for r in vector_store.search(q, body.k, f):
                seen = best.get(r["concept_id"])
                if seen is None or r["score"] > seen["score"]:
                    best[r["concept_id"]] = r
        merged = sorted(best.values(), key=lambda r: r["score"], reverse=True)[: body.k]
        return [{**r, "rank": i + 1} for i, r in enumerate(merged)]

    try:
        results = run(filters)
        method = (
            f"Similarity search, ATC7 {', '.join(body.atc7_codes)} only"
            if body.atc7_codes
            else "Similarity search"
        )
        if not results and body.atc7_codes and body.atc7_fallback:
            filters.atc7_codes = ()
            results = run(filters)
            method = "No ATC7 match, similarity search"
    except CollectionMissing as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    if len(queries) > 1:
        method += f" · {len(queries)} search texts"
    return SearchResponse(results=results, method=method, collection=cfg.collection_name)
