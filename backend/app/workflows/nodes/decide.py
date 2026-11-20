"""Last step for terms nothing else could map confidently: every candidate found on the way and
every earlier verdict go to the stronger model, which makes one final, informed choice."""

from pydantic import BaseModel, ConfigDict, Field

from app.llm.structured import Reranker
from app.workflows.context import NodeContext
from app.workflows.enrich import with_context
from app.workflows.registry import node
from app.workflows.state import AutomapState

ROUTES = {
    "escalated": "decided by the stronger model after an unsure first answer",
    "judge": "decided by a judge after the voters disagreed",
}


class DecideParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_candidates: int = Field(default=45, ge=5, le=80)
    # Candidates shown with their parents, attributes and synonyms (earlier picks come first)
    context_top: int = Field(default=10, ge=0, le=30)


def _verdict(attempt: dict) -> str:
    concept = attempt.get("concept")
    pick = (
        f"{concept['concept_name']} (ID {concept['concept_id']})"
        if concept
        else "no suitable concept"
    )
    details = []
    if attempt.get("confidence") is not None:
        details.append(f"confidence {attempt['confidence']}")
    if attempt.get("match_type"):
        details.append(f"{attempt['match_type']} match")
    if attempt.get("route") in ROUTES:
        details.append(ROUTES[attempt["route"]])
    line = f"- {attempt.get('step', 'earlier step')}: {pick}"
    if details:
        line += f" [{', '.join(details)}]"
    if attempt.get("rationale"):
        line += f": {attempt['rationale']}"
    return line


def evidence_notes(state: AutomapState) -> list[str]:
    notes = [
        "This term went through several attempts without a confident match. Earlier verdicts "
        "(they may be wrong; judge the candidates yourself):"
    ]
    notes += [_verdict(a) for a in state.get("attempts") or []]
    if state.get("rewritten_queries"):
        queries = "; ".join(repr(q) for q in state["rewritten_queries"])
        notes.append(f"Searches tried besides the term itself: {queries}.")
    notes.append(
        "Choose the best-supported concept from the full candidate list below, which combines "
        "every search. Prefer an exact match; otherwise the closest broader concept that is "
        "certainly true. Rate your confidence honestly."
    )
    return notes


def all_candidates(state: AutomapState, limit: int) -> list[dict]:
    """The first search, the rewritten searches and the agent's finds, without duplicates.
    Earlier picks come first, so none is cut off."""
    merged: dict[int, dict] = {}
    picked = [a["concept"] for a in state.get("attempts") or [] if a.get("concept")]
    for c in [*picked, *(state.get("candidates") or []), *(state.get("agent_candidates") or [])]:
        merged.setdefault(c["concept_id"], c)
    return list(merged.values())[:limit]


@node(
    "decide_with_evidence",
    params=DecideParams,
    description="Last resort: the stronger model chooses from every candidate found, seeing all "
    "earlier verdicts",
)
async def decide_with_evidence(state: AutomapState, ctx: NodeContext, params: DecideParams) -> dict:
    candidates = all_candidates(state, params.max_candidates)
    if not candidates:
        return {"_summary": "nothing to decide on (no candidates)"}
    settings = ctx.settings
    judge = ctx.extras.get("final_reranker") or Reranker(
        settings.rerank_escalate_model.strip() or ctx.llm_model,
        effort=settings.rerank_escalate_effort or None,
    )
    term = state.get("source_name") or state.get("query", "")
    try:
        result = await judge.rerank(
            term,
            await with_context(ctx, candidates, params.context_top),
            bool(state.get("drug_specific")),
            translation=state.get("translated_query"),
            notes=[*(state.get("notes") or []), *evidence_notes(state)],
            context=state.get("vocabulary_context"),
        )
    except Exception as exc:
        ctx.logger.warning("Final decision failed for %r: %s", term, exc)
        return {"_summary": f"final decision failed ({type(exc).__name__}); earlier result stands"}
    if result.index is None:
        return {"_summary": "final decision: no suitable concept; earlier result stands"}
    selected = candidates[result.index]
    additional = [candidates[i] for i in getattr(result, "additional", None) or []]
    return {
        "candidates": candidates,
        "selected": selected,
        "selected_concepts": [selected, *additional] if additional else [],
        "confidence": result.confidence,
        "match_type": getattr(result, "match_type", None),
        "rationale": f"[final decision on all evidence] {result.rationale or ''}".strip(),
        "method": "auto_final",
        "outcome": "selected",
        "_summary": f"final decision: '{selected['concept_name']}' (confidence {result.confidence}) "
        f"from {len(candidates)} candidates",
    }
