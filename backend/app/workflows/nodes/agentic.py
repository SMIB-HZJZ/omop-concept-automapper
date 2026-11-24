import asyncio

from pydantic import BaseModel, ConfigDict, Field

from app.llm.agents.base import gateway_model
from app.llm.agents.mapping_agent import AgentDeps, run_mapping_agent
from app.workflows import scope
from app.workflows.context import NodeContext
from app.workflows.registry import node
from app.workflows.state import AutomapState


class AgenticParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_results: int = Field(default=20, ge=5, le=50)


@node(
    "agentic_refine",
    params=AgenticParams,
    description="Pydantic AI agent reformulates the search and inspects the hierarchy; its pick "
    "replaces the first one only when it is more confident",
)
async def agentic_refine(state: AutomapState, ctx: NodeContext, params: AgenticParams) -> dict:
    deps = AgentDeps(
        searcher=ctx.searcher,
        get_concepts=ctx.get_concepts,
        get_details=ctx.get_details or (lambda concept_id: None),
        domains=scope.targets(state)[0],
        vocabularies=scope.targets(state)[1],
        restrict=scope.restricted(state),
        drug_specific=bool(state.get("drug_specific")),
        atc7_codes=state.get("atc7_codes") or [],
        max_results=params.max_results,
        context=state.get("vocabulary_context"),
        report=lambda what: ctx.report(state, None, "agentic_refine", what),
    )
    term = state.get("source_name") or state.get("query", "")
    model = ctx.agent_model or gateway_model(ctx.llm_model)
    runner = ctx.run_agent or run_mapping_agent
    first = state.get("selected")
    first_confidence = state.get("confidence") or 0
    keep = f"kept the first pick '{first['concept_name']}' ({first_confidence})" if first else ""
    try:
        decision = await runner(
            model, term, deps, state.get("candidates") or [], state.get("translated_query")
        )
    except Exception as exc:
        # The agent is a second chance: its failure leaves the first result as it was
        ctx.logger.warning("Mapping agent failed for %r: %s", term, exc)
        return {"_summary": f"agent error ({type(exc).__name__}){'; ' + keep if keep else ''}"}
    found_by_agent = [
        c
        for c in deps.seen.values()
        if "concept_name" in c and c.get("standard_concept") in (None, "S")
    ]
    attempt = {
        "step": "agent retry",
        "concept": deps.seen.get(decision.concept_id) if decision.concept_id else None,
        "confidence": decision.confidence,
        "match_type": None,
        "rationale": decision.rationale,
    }
    evidence = {"attempts": [attempt], "agent_candidates": found_by_agent}
    if decision.concept_id is None:
        if first:
            return {**evidence, "_summary": f"agent found no match; {keep}"}
        # The first pass's outcome (no_match, no_candidates or error) stands
        return {
            **evidence,
            "rationale": decision.rationale,
            "_summary": f"agent found no match: {decision.rationale}",
        }
    if first and (decision.confidence or 0) <= first_confidence:
        return {
            **evidence,
            "_summary": f"agent picked concept {decision.concept_id} ({decision.confidence}), "
            f"no surer; {keep}",
        }
    selected = deps.seen.get(decision.concept_id)
    if selected is None or "concept_name" not in selected:
        found = await asyncio.to_thread(ctx.get_concepts, [decision.concept_id])
        selected = found[0] if found else {"concept_id": decision.concept_id, "concept_name": "?"}
    return {
        **evidence,
        "selected": selected,
        # One concept replaces a combined first pick; the agent states no match type
        "selected_concepts": [],
        "match_type": None,
        "confidence": decision.confidence,
        "rationale": decision.rationale,
        "method": "auto_agentic",
        "outcome": "selected",
        "_summary": f"agent picked '{selected['concept_name']}' (confidence {decision.confidence})",
    }
