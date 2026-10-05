import asyncio

from pydantic import BaseModel, ConfigDict, Field

from app.vector.store import SearchFilters
from app.workflows.context import NodeContext
from app.workflows.registry import node
from app.workflows.state import AutomapState


class RetrieveParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    k_default: int = Field(default=15, ge=1, le=200)
    k_drug: int = Field(default=30, ge=1, le=200)
    use_atc7: bool = True
    # When the ATC7-filtered search finds nothing, search again without the ATC7 filter
    atc7_fallback: bool = True


@node(
    "retrieve", params=RetrieveParams, description="Vector search for candidate standard concepts"
)
async def retrieve(state: AutomapState, ctx: NodeContext, params: RetrieveParams) -> dict:
    drug = bool(state.get("drug_specific"))
    k = params.k_drug if drug else params.k_default
    atc7 = (state.get("atc7_codes") or []) if (drug and params.use_atc7) else []
    filters = SearchFilters(
        domains=state.get("domains") or [],
        vocabulary_id=state.get("vocabulary_id"),
        atc7_codes=atc7,
    )
    query = state.get("query") or state.get("source_name", "")
    results = await asyncio.to_thread(ctx.searcher.search, query, k, filters)
    note = f"ATC7 {', '.join(atc7)}" if atc7 else "similarity"
    if not results and atc7 and params.atc7_fallback:
        filters.atc7_codes = ()
        results = await asyncio.to_thread(ctx.searcher.search, query, k, filters)
        note = "no ATC7 match, fell back to similarity"
    update: dict = {
        "candidates": results,
        "method": "auto_drug" if drug else "auto_standard",
        "_summary": f"{len(results)} candidates for '{query}' (k={k}, {note})",
    }
    if not results:
        update["outcome"] = "no_candidates"
    return update
