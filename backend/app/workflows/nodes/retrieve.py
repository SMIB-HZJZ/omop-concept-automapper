import asyncio

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.drug_matching import BRANDED_CLASSES
from app.vector.store import SearchFilters
from app.workflows import scope
from app.workflows.context import NodeContext
from app.workflows.registry import node
from app.workflows.state import AutomapState


class RetrieveParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Candidates passed to the reranker. One value for all domains: drugs reach the search only
    # when the structured drug_strength match fails.
    k: int = Field(default=30, ge=1, le=200)
    use_atc7: bool = True
    # When the ATC7-filtered search finds nothing, search again without the ATC7 filter. A wrong
    # or outdated ATC7 code in the source would otherwise leave no candidates at all; the
    # reranker still judges the unfiltered ones.
    atc7_fallback: bool = True
    # Keyword (trigram) search on names and synonyms next to the vector search, fused by rank.
    # Off by default: in the 2026-11 evaluation fusion lowered R@1 by ~10 points for good
    # embedding models and did not raise R@30.
    keyword: bool = False
    # Reciprocal rank fusion constant (larger = flatter weighting of ranks)
    rrf_k: int = Field(default=60, ge=1)
    # domain_mode "prefer": how many candidates from outside the domains and vocabularies are added
    other_domains: int = Field(default=5, ge=0, le=50)
    # Drugs: also search the structure written like an RxNorm name (set by match_drug)
    drug_search_text: bool = True

    @model_validator(mode="before")
    @classmethod
    def _legacy_k(cls, data):
        """Pipelines written before ``k`` used k_default (and k_drug): k_default becomes k."""
        if isinstance(data, dict) and ("k_default" in data or "k_drug" in data):
            data = dict(data)
            legacy = data.pop("k_default", None)
            drug = data.pop("k_drug", None)
            data.setdefault("k", legacy if legacy is not None else drug)
        return data


def excluded_drug_classes(state: AutomapState) -> tuple[str, ...]:
    """Concept classes every search for this term leaves out: branded drugs when the drug step
    decided the term has no brand (``meta.drug_exclude_branded``, see MatchDrugParams)."""
    if state.get("drug_specific") and (state.get("meta") or {}).get("drug_exclude_branded"):
        return BRANDED_CLASSES
    return ()


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


def _fuse(result_lists: list[list[dict]], k: int, rrf_k: int) -> list[dict]:
    """Reciprocal rank fusion: concepts ranked high by several searches come first."""
    fused: dict[int, float] = {}
    first: dict[int, dict] = {}
    for results in result_lists:
        for rank, r in enumerate(results, 1):
            cid = r["concept_id"]
            fused[cid] = fused.get(cid, 0.0) + 1.0 / (rrf_k + rank)
            # keep the vector score when there is one (keyword scores are on another scale)
            if cid not in first or r.get("source") == "vector":
                first[cid] = r
    order = sorted(fused, key=lambda cid: fused[cid], reverse=True)[:k]
    return [{**first[cid], "rank": i + 1} for i, cid in enumerate(order)]


@node(
    "retrieve",
    params=RetrieveParams,
    description="Vector search for candidate standard concepts (optionally fused with keyword search)",
)
async def retrieve(state: AutomapState, ctx: NodeContext, params: RetrieveParams) -> dict:
    drug = bool(state.get("drug_specific"))
    k = params.k
    atc7 = (state.get("atc7_codes") or []) if (drug and params.use_atc7) else []
    domains, vocabularies = scope.targets(state)
    filters = SearchFilters(
        domains=domains,
        vocabularies=vocabularies,
        vocabulary_id=state.get("vocabulary_id"),
        atc7_codes=atc7,
        exclude_concept_classes=excluded_drug_classes(state),
    )
    query = state.get("query") or state.get("source_name", "")
    queries = [query]
    extra = [state.get("translated_query")]
    if drug and params.drug_search_text:
        # e.g. "acetaminophen 500 MG Oral Tablet": RxNorm drug names follow this pattern
        extra.append((state.get("meta") or {}).get("drug_search_text"))
    for q in extra:
        if q and q.strip().lower() not in {x.strip().lower() for x in queries}:
            queries.append(q)
    keyword = params.keyword and ctx.lexical is not None

    async def search(f: SearchFilters, limit: int) -> list[dict]:
        calls = [asyncio.to_thread(ctx.searcher.search, q, limit, f) for q in queries]
        if keyword:
            calls += [asyncio.to_thread(ctx.lexical.keyword_search, q, limit, f) for q in queries]
        found = await asyncio.gather(*calls)
        vector = [[{**r, "source": "vector"} for r in lst] for lst in found[: len(queries)]]
        if not keyword:
            return _merge(vector, limit)
        lexical = [[{**r, "source": "keyword"} for r in lst] for lst in found[len(queries) :]]
        return _fuse(vector + lexical, limit, params.rrf_k)

    results = await search(filters, k)
    note = f"ATC7 {', '.join(atc7)}" if atc7 else ("vector+keyword" if keyword else "similarity")
    if not results and atc7 and params.atc7_fallback:
        filters.atc7_codes = ()
        results = await search(filters, k)
        note = "no ATC7 match, fell back to similarity"
    if filters.exclude_concept_classes:
        note += ", no branded drugs (the source names no brand)"
    if scope.preferred(state) and params.other_domains:
        others = await search(
            SearchFilters(
                vocabulary_id=filters.vocabulary_id,
                atc7_codes=filters.atc7_codes,
                exclude_concept_classes=filters.exclude_concept_classes,
            ),
            k,
        )
        seen = {r["concept_id"] for r in results}
        extra = [r for r in others if r["concept_id"] not in seen and not scope.fits(state, r)]
        results = results + extra[: params.other_domains]
        note += f", +{min(len(extra), params.other_domains)} from outside the preferences"
    structured = state.get("structured_candidates") or []
    if structured:
        # Tied deterministic matches go first, the search results fill up to k
        ids = {c["concept_id"] for c in structured}
        results = [{**c, "score": 1.0} for c in structured] + [
            r for r in results if r["concept_id"] not in ids
        ]
        results = results[: max(len(results), len(structured))]
    results = [{**r, "rank": i + 1} for i, r in enumerate(results)]
    if len(queries) > 1:
        query = " | ".join(queries)
    update: dict = {
        "candidates": results,
        "method": "auto_drug" if drug else "auto_standard",
        "_summary": f"{len(results)} candidates for '{query}' (k={k}, {note})",
    }
    if scope.preferred(state):
        update["notes"] = [
            *(state.get("notes") or []),
            f"Preferred {scope.describe(state)}. Choose a concept outside them only if it fits "
            "clearly better.",
        ]
    if not results:
        update["outcome"] = "no_candidates"
    return update
