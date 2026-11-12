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
    # When the ATC7-filtered search finds nothing, search again without the ATC7 filter.
    # Off by default (as in the Streamlit app): a drug outside its ATC7 class is a wrong match.
    atc7_fallback: bool = False


def _merge(result_lists: list[list[dict]], k: int) -> list[dict]:
    """Union of several searches: best score per concept, top k, re-ranked."""
    best: dict[int, dict] = {}
    for results in result_lists:
        for r in results:
            seen = best.get(r["concept_id"])
            if seen is None or r["score"] > seen["score"]:
                best[r["concept_id"]] = r
    merged = sorted(best.values(), key=lambda r: r["score"], reverse=True)[:k]
    return [{**r, "rank": i + 1} for i, r in enumerate(merged)]


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
    queries = [query]
    translated = state.get("translated_query")
    if translated and translated.strip().lower() != query.strip().lower():
        queries.append(translated)

    async def search(f: SearchFilters) -> list[dict]:
        found = await asyncio.gather(
            *(asyncio.to_thread(ctx.searcher.search, q, k, f) for q in queries)
        )
        return _merge(found, k)

    results = await search(filters)
    structured = state.get("structured_candidates") or []
    if structured:
        # Tied deterministic drug matches go first, the similarity results fill up to k
        ids = {c["concept_id"] for c in structured}
        results = [{**c, "score": 1.0} for c in structured] + [
            r for r in results if r["concept_id"] not in ids
        ]
        results = [{**r, "rank": i + 1} for i, r in enumerate(results[: max(k, len(structured))])]
    note = f"ATC7 {', '.join(atc7)}" if atc7 else "similarity"
    if not results and atc7 and params.atc7_fallback:
        filters.atc7_codes = ()
        results = await search(filters)
        note = "no ATC7 match, fell back to similarity"
    if len(queries) > 1:
        query = " | ".join(queries)
    update: dict = {
        "candidates": results,
        "method": "auto_drug" if drug else "auto_standard",
        "_summary": f"{len(results)} candidates for '{query}' (k={k}, {note})",
    }
    if not results:
        update["outcome"] = "no_candidates"
    return update
