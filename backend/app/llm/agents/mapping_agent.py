"""Pydantic AI agent that refines a low-confidence mapping using search and hierarchy tools."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field

from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.models import Model

from app.llm.prompts import AGENT_INSTRUCTIONS, CONCEPT_SYSTEM_PROMPT, DRUG_SYSTEM_PROMPT
from app.llm.schemas import MappingDecision
from app.vector.store import SearchFilters, VectorSearcher


@dataclass
class AgentDeps:
    searcher: VectorSearcher
    get_concepts: Callable[[list[int]], list[dict]]
    get_ancestors: Callable[[int, int], list[dict]]
    domains: list[str]
    drug_specific: bool
    atc7_codes: list[str] = field(default_factory=list)
    seen: dict[int, dict] = field(default_factory=dict)
    max_results: int = 20


def _brief(c: dict) -> dict:
    return {
        "concept_id": c["concept_id"],
        "concept_name": c["concept_name"],
        "domain_id": c.get("domain_id"),
        "concept_class_id": c.get("concept_class_id"),
        "vocabulary_id": c.get("vocabulary_id"),
        "standard": c.get("standard_concept") == "S",
        **({"score": round(c["score"], 3)} if "score" in c else {}),
    }


mapping_agent = Agent(
    output_type=MappingDecision,
    deps_type=AgentDeps,
    retries=2,
    name="mapping_agent",
)


@mapping_agent.instructions
def _instructions(ctx: RunContext[AgentDeps]) -> str:
    criteria = DRUG_SYSTEM_PROMPT if ctx.deps.drug_specific else CONCEPT_SYSTEM_PROMPT
    domains = ", ".join(ctx.deps.domains) or "any"
    return f"{AGENT_INSTRUCTIONS}\nTarget domains: {domains}.\n\nEvaluation criteria:\n{criteria}"


@mapping_agent.tool
async def search_concepts(
    ctx: RunContext[AgentDeps],
    query: str,
    domains: list[str] | None = None,
    use_atc7_filter: bool = False,
    k: int = 15,
) -> list[dict]:
    """Semantic search over standard OMOP concepts.

    Args:
        query: Search text, ideally in English OMOP-style wording.
        domains: OMOP domains to search (defaults to the target domains).
        use_atc7_filter: Restrict to drugs with the source's ATC7 code(s), if any were found.
        k: Number of results (max 30).
    """
    filters = SearchFilters(
        domains=domains or ctx.deps.domains,
        atc7_codes=ctx.deps.atc7_codes if use_atc7_filter else (),
    )
    results = await asyncio.to_thread(ctx.deps.searcher.search, query, min(k, 30), filters)
    for r in results:
        ctx.deps.seen[r["concept_id"]] = r
    return [_brief(r) for r in results]


@mapping_agent.tool
async def get_concept(ctx: RunContext[AgentDeps], concept_id: int) -> dict | None:
    """Look up one OMOP concept by id."""
    found = await asyncio.to_thread(ctx.deps.get_concepts, [concept_id])
    if not found:
        return None
    ctx.deps.seen[concept_id] = found[0]
    return _brief(found[0])


@mapping_agent.tool
async def get_ancestors(ctx: RunContext[AgentDeps], concept_id: int, levels: int = 2) -> list[dict]:
    """Ancestors of a concept up to `levels` steps up the hierarchy (useful to find a less specific concept)."""
    ancestors = await asyncio.to_thread(ctx.deps.get_ancestors, concept_id, min(levels, 4))
    for a in ancestors:
        ctx.deps.seen.setdefault(a["concept_id"], a)
    return [{**_brief(a), "levels": a.get("levels")} for a in ancestors]


@mapping_agent.output_validator
def _validate_choice(ctx: RunContext[AgentDeps], output: MappingDecision) -> MappingDecision:
    if output.concept_id is None:
        return output
    concept = ctx.deps.seen.get(output.concept_id)
    if concept is None:
        raise ModelRetry(
            f"concept_id {output.concept_id} was not returned by any tool; choose one you have seen or null"
        )
    if concept.get("standard_concept") not in (None, "S"):
        raise ModelRetry(f"concept_id {output.concept_id} is not a standard concept")
    if output.confidence is None:
        raise ModelRetry("confidence is required when a concept is chosen")
    return output


async def run_mapping_agent(
    model: Model | str, term: str, deps: AgentDeps, initial_candidates: list[dict]
) -> MappingDecision:
    for c in initial_candidates:
        deps.seen[c["concept_id"]] = c
    listing = "\n".join(
        f"- {c['concept_id']}: {c['concept_name']} ({c.get('domain_id')}, {c.get('concept_class_id')})"
        for c in initial_candidates[:15]
    )
    prompt = f"Source term: {term}\n\nInitial candidates from vector search:\n{listing or '(none)'}"
    result = await mapping_agent.run(prompt, deps=deps, model=model)
    return result.output
