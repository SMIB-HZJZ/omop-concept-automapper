from app.workflows.context import NodeContext
from app.workflows.registry import node
from app.workflows.state import AutomapState


@node(
    "llm_rerank", description="LLM picks the best candidate and rates its confidence (Instructor)"
)
async def llm_rerank(state: AutomapState, ctx: NodeContext, params) -> dict:
    candidates = state.get("candidates") or []
    if not candidates:
        return {"selected": None, "confidence": None, "_summary": "no candidates to rerank"}
    term = state.get("source_name") or state.get("query", "")
    try:
        result = await ctx.reranker.rerank(term, candidates, bool(state.get("drug_specific")))
    except Exception as exc:
        ctx.logger.warning("Reranker failed for %r: %s", term, exc)
        return {
            "selected": None,
            "confidence": None,
            "outcome": "error",
            "error": f"{type(exc).__name__}: {exc}",
            "_summary": f"reranker error: {exc}",
        }
    if result.index is None:
        return {
            "selected": None,
            "confidence": None,
            "outcome": "no_match",
            "_summary": "LLM found no match",
        }
    selected = candidates[result.index]
    return {
        "selected": selected,
        "confidence": result.confidence,
        "outcome": "selected",
        "_summary": f"picked #{result.index} '{selected['concept_name']}' (confidence {result.confidence})",
    }
