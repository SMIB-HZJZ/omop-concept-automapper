import re

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import CurrentUser, SessionDep
from app.llm.embeddings import Embedder
from app.schemas.api import ConceptLookup, ConceptOut, SearchRequest, SearchResponse
from app.services import concepts as concept_service
from app.services.settings_store import get_effective_config
from app.vector.store import CollectionMissing, PgVectorStore, SearchFilters

router = APIRouter(tags=["concepts"])


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


@router.get("/concepts/{concept_id}", response_model=ConceptOut)
def get_concept(concept_id: int, _: CurrentUser, session: SessionDep):
    found = concept_service.get_concepts(session, [concept_id])
    if not found:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Concept ID {concept_id} not found")
    return found[0]


@router.post("/search", response_model=SearchResponse)
def search(body: SearchRequest, _: CurrentUser, session: SessionDep):
    cfg = get_effective_config(session)
    vector_store = PgVectorStore(
        session, cfg.collection_name, Embedder(cfg.embedding_model, cfg.embedding_dims)
    )
    filters = SearchFilters(
        domains=body.domains, vocabulary_id=body.vocabulary_id or None, atc7_codes=body.atc7_codes
    )
    try:
        results = vector_store.search(body.query.strip(), body.k, filters)
    except CollectionMissing as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    method = (
        f"ATC7 filtered ({', '.join(body.atc7_codes)})"
        if body.atc7_codes
        else "Standard similarity"
    )
    return SearchResponse(results=results, method=method, collection=cfg.collection_name)
