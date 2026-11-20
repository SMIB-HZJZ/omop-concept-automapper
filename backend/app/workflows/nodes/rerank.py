import asyncio

from pydantic import BaseModel, ConfigDict, Field

from app.workflows.context import NodeContext
from app.workflows.registry import node
from app.workflows.state import AutomapState


class RerankParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Reviewed mappings of similar source terms shown to the LLM as examples (0 = none)
    examples: int = Field(default=5, ge=0, le=20)


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
        result = await ctx.reranker.rerank(
            term, candidates, bool(state.get("drug_specific")), **extra
        )
    except Exception as exc:
        ctx.logger.warning("Reranker failed for %r: %s", term, exc)
        return {
            "selected": None,
            "confidence": None,
            "outcome": "error",
            "error": f"{type(exc).__name__}: {exc}",
            "_summary": f"reranker error: {exc}",
        }
    route = getattr(result, "route", None)
    rationale = result.rationale
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
    if result.index is None:
        return {
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
        **({"meta": meta} if meta else {}),
        "selected": selected,
        "selected_concepts": [selected, *additional] if additional else [],
        "confidence": result.confidence,
        "match_type": getattr(result, "match_type", None),
        "outcome": "selected",
        "rationale": rationale,
        "_summary": f"picked #{result.index} '{selected['concept_name']}' (confidence {result.confidence})"
        + (f", {route}" if route else ""),
    }
