"""Second search for terms the first pass could not map confidently: the LLM rewrites the query
(abbreviations spelled out, synonyms, OMOP wording) and the new candidates go to the reranker."""

import asyncio

from pydantic import BaseModel, ConfigDict, Field

from app.llm.structured import QueryRewriter
from app.vector.store import SearchFilters
from app.workflows import scope
from app.workflows.context import NodeContext
from app.workflows.nodes.retrieve import excluded_drug_classes
from app.workflows.registry import router
from app.workflows.state import AutomapState


class RewriteParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Results per rewritten query, and new candidates added at most
    k: int = Field(default=15, ge=1, le=50)
    max_new: int = Field(default=20, ge=1, le=60)


@router(
    "rewrite_search",
    routes={"rerank", "skip"},
    params=RewriteParams,
    description="LLM rewrites the search query; new candidates go back to the reranker",
)
async def rewrite_search(state: AutomapState, ctx: NodeContext, params: RewriteParams):
    term = state.get("source_name") or state.get("query", "")
    candidates = state.get("candidates") or []
    rewriter = ctx.rewriter or QueryRewriter(
        ctx.llm_model, effort=ctx.settings.llm_reasoning_effort
    )
    try:
        queries = await rewriter.rewrite(
            term,
            translation=state.get("translated_query"),
            context=state.get("vocabulary_context"),
            shown=[c["concept_name"] for c in candidates],
        )
    except Exception as exc:
        ctx.logger.warning("Query rewrite failed for %r: %s", term, exc)
        return "skip", {"_summary": f"rewrite failed ({type(exc).__name__})"}
    if not queries:
        return "skip", {"_summary": "no better query"}
    drug = bool(state.get("drug_specific"))
    domains, vocabularies = scope.targets(state)
    filters = SearchFilters(
        domains=domains,
        vocabularies=vocabularies,
        vocabulary_id=state.get("vocabulary_id"),
        atc7_codes=(state.get("atc7_codes") or []) if drug else [],
        # Like retrieve: no branded drugs for a source that names no brand
        exclude_concept_classes=excluded_drug_classes(state),
    )
    found = await asyncio.gather(
        *(asyncio.to_thread(ctx.searcher.search, q, params.k, filters) for q in queries)
    )
    seen = {c["concept_id"] for c in candidates}
    new: list[dict] = []
    # Round-robin over the queries, so each contributes its best results first
    for rank in range(params.k):
        for results in found:
            if rank < len(results) and results[rank]["concept_id"] not in seen:
                seen.add(results[rank]["concept_id"])
                new.append({**results[rank], "source": "rewrite"})
    new = new[: params.max_new]
    listed = "; ".join(repr(q) for q in queries)
    if not new:
        return "skip", {
            "rewritten_queries": queries,
            "_summary": f"searched {listed}: nothing new",
        }
    merged = [*candidates, *new]
    return "rerank", {
        "rewritten_queries": queries,
        "candidates": [{**c, "rank": i + 1} for i, c in enumerate(merged)],
        "_summary": f"searched {listed}: {len(new)} new candidates",
    }
