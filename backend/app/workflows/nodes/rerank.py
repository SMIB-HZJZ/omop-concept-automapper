import asyncio

from pydantic import BaseModel, ConfigDict, Field

from app.llm.structured import EscalatingReranker
from app.workflows.context import NodeContext
from app.workflows.enrich import with_context
from app.workflows.registry import node
from app.workflows.state import AutomapState


class RerankParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Reviewed mappings of similar source terms shown to the LLM as examples (0 = none)
    examples: int = Field(default=5, ge=0, le=20)
    # A second rerank (after rewrite_search): its pick replaces the first only when it is more
    # confident; if it finds nothing or fails, the first result stands
    keep_better: bool = False
    # Show the first N candidates with their parents, attributes and synonyms (0 = names only)
    context_top: int = Field(default=0, ge=0, le=30)
    # False: the first model only, even when the context's reranker escalates unsure answers
    escalate: bool = True


@node(
    "llm_rerank",
    params=RerankParams,
    description="LLM picks the best candidate and rates its confidence (Instructor)",
)
async def llm_rerank(state: AutomapState, ctx: NodeContext, params: RerankParams) -> dict:
    candidates = state.get("candidates") or []
    if not candidates:
        return {"selected": None, "confidence": None, "_summary": "no candidates to rerank"}
    term = state.get("source_name") or state.get("query", "")
    first = state.get("selected") if params.keep_better else None
    first_confidence = state.get("confidence") or 0
    keep = f"kept the first pick '{first['concept_name']}' ({first_confidence})" if first else ""
    try:
        translation = state.get("translated_query")
        extra: dict = {"translation": translation} if translation else {}
        if state.get("vocabulary_context"):
            extra["context"] = state["vocabulary_context"]
        if state.get("notes"):
            extra["notes"] = state["notes"]
        if params.examples and ctx.lexical is not None:
            examples = await asyncio.to_thread(
                ctx.lexical.similar_examples, term, state.get("source_id"), params.examples
            )
            if examples:
                extra["examples"] = examples
        reranker = ctx.reranker
        if isinstance(reranker, EscalatingReranker):
            if params.escalate:
                extra["on_escalate"] = lambda: ctx.report(
                    state, None, "llm_rerank", "Re-check by a stronger model"
                )
            else:
                reranker = reranker.first
        shown = await with_context(ctx, candidates, params.context_top)
        result = await reranker.rerank(term, shown, bool(state.get("drug_specific")), **extra)
    except Exception as exc:
        ctx.logger.warning("Reranker failed for %r: %s", term, exc)
        if first:
            return {"_summary": f"reranker error ({type(exc).__name__}); {keep}"}
        return {
            "selected": None,
            "confidence": None,
            "outcome": "error",
            "error": f"{type(exc).__name__}: {exc}",
            "_summary": f"reranker error: {exc}",
        }
    route = getattr(result, "route", None)
    rationale = result.rationale
    attempt = {
        "step": "second AI pick, after rewritten searches" if params.keep_better else "AI pick",
        "concept": candidates[result.index] if result.index is not None else None,
        "confidence": result.confidence,
        "match_type": getattr(result, "match_type", None),
        "rationale": result.rationale,
        "route": route,
        "votes": getattr(result, "votes", None),
    }
    meta: dict = {}
    if route:
        # Kept with the mapping: terms the judge decided are the ones to review first
        tag = {
            "agreed": "voters agreed",
            "judge": "voters disagreed, judge decided",
            "escalated": "unsure first answer, decided by the stronger model",
        }.get(route)
        if tag:
            rationale = f"[{tag}] {rationale or ''}".strip()
        meta = {"rerank_route": route, "rerank_votes": result.votes}
    if first and (result.index is None or (result.confidence or 0) <= first_confidence):
        found = "no match" if result.index is None else f"confidence {result.confidence}"
        return {"attempts": [attempt], "_summary": f"{found}, no surer; {keep}"}
    if result.index is None:
        return {
            "attempts": [attempt],
            **({"meta": meta} if meta else {}),
            "selected": None,
            "confidence": None,
            "outcome": "no_match",
            "rationale": rationale,
            "_summary": "LLM found no match" + (f" ({route})" if route else ""),
        }
    selected = candidates[result.index]
    additional = [candidates[i] for i in getattr(result, "additional", None) or []]
    return {
        "attempts": [attempt],
        **({"meta": meta} if meta else {}),
        # A pick from the rewritten searches is told apart in the precision-by-method statistics
        **({"method": "auto_rewritten"} if params.keep_better else {}),
        "selected": selected,
        "selected_concepts": [selected, *additional] if additional else [],
        "confidence": result.confidence,
        "match_type": getattr(result, "match_type", None),
        "outcome": "selected",
        "rationale": rationale,
        "_summary": f"picked #{result.index} '{selected['concept_name']}' (confidence {result.confidence})"
        + (f", {route}" if route else ""),
    }
