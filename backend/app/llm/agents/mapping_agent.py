"""Pydantic AI agent that refines a low-confidence mapping using search and hierarchy tools."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field

from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.models import Model
from pydantic_ai.usage import UsageLimits

from app.core.config import get_settings
from app.llm.prompts import AGENT_INSTRUCTIONS, agent_criteria
from app.llm.schemas import MappingDecision
from app.vector.store import SearchFilters, VectorSearcher


@dataclass
class AgentDeps:
    searcher: VectorSearcher
    get_concepts: Callable[[list[int]], list[dict]]
    # Full details of one concept: parents, children, defining attributes, synonyms
    get_details: Callable[[int], dict | None]
    domains: list[str]
    drug_specific: bool
    # Target OMOP vocabularies (empty: any)
    vocabularies: list[str] = field(default_factory=list)
    # domain_mode "restrict": searches never leave the target domains and vocabularies
    restrict: bool = False
    atc7_codes: list[str] = field(default_factory=list)
    seen: dict[int, dict] = field(default_factory=dict)
    max_results: int = 20
    # Name and description of the source vocabulary
    context: str | None = None
    # Live progress: told what the agent is doing (a short label)
    report: Callable[[str], None] | None = None

    def tell(self, what: str) -> None:
        if self.report is not None:
            try:
                self.report(what)
            except Exception:
                pass


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
    retries=1,  # per run: AGENT_OUTPUT_RETRIES for its answer, one for tool argument errors
    name="mapping_agent",
)


@mapping_agent.instructions
def _instructions(ctx: RunContext[AgentDeps]) -> str:
    domains = ", ".join(ctx.deps.domains) or "any"
    vocabularies = ", ".join(ctx.deps.vocabularies) or "any"
    kind = "only these" if ctx.deps.restrict else "preferred"
    return (
        f"{AGENT_INSTRUCTIONS}\n\nTarget domains ({kind}): {domains}. "
        f"Target vocabularies ({kind}): {vocabularies}.\n\n"
        f"{agent_criteria(ctx.deps.drug_specific)}"
    )


@mapping_agent.tool
async def search_concepts(
    ctx: RunContext[AgentDeps],
    query: str,
    domains: list[str] | None = None,
    vocabularies: list[str] | None = None,
    use_atc7_filter: bool = False,
    k: int = 15,
) -> list[dict]:
    """Semantic search over standard OMOP concepts.

    Args:
        query: Search text, ideally in English OMOP-style wording.
        domains: OMOP domains to search (defaults to the target domains).
        vocabularies: OMOP vocabularies to search, e.g. ["LOINC"] (defaults to the target
            vocabularies).
        use_atc7_filter: Restrict to drugs with the source's ATC7 code(s), if any were found.
        k: Number of results (max 30).
    """
    ctx.deps.tell(f"Agent retry: searching '{query}'")
    deps = ctx.deps
    domains = domains or deps.domains
    vocabularies = vocabularies or deps.vocabularies
    if deps.restrict:
        # Narrower searches are fine; leaving the chosen domains or vocabularies is not
        if deps.domains:
            domains = [d for d in domains if d in deps.domains] or deps.domains
        if deps.vocabularies:
            vocabularies = [v for v in vocabularies if v in deps.vocabularies] or deps.vocabularies
    filters = SearchFilters(
        domains=domains,
        vocabularies=vocabularies,
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
async def get_concept_details(ctx: RunContext[AgentDeps], concept_id: int) -> dict | None:
    """What a concept is and where it sits: synonyms, direct parents (broader concepts), direct
    children (narrower concepts) and defining attributes (e.g. finding site, morphology, LOINC
    component and system). Use it to tell an exact match from a broader or narrower one."""
    ctx.deps.tell("Agent retry: checking a concept's details")
    details = await asyncio.to_thread(ctx.deps.get_details, concept_id)
    if details is None:
        return None
    for c in [details["concept"], *details["parents"], *details["children"]]:
        ctx.deps.seen.setdefault(c["concept_id"], c)
    return {
        **_brief(details["concept"]),
        "synonyms": details["synonyms"],
        "parents": [_brief(c) for c in details["parents"]],
        "children": [_brief(c) for c in details["children"]],
        "child_count": details["child_count"],
        "attributes": [
            {"relationship": r["relationship_id"], "concept_name": r["concept_name"]}
            for r in details["attributes"]
        ],
    }


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
    model: Model | str,
    term: str,
    deps: AgentDeps,
    initial_candidates: list[dict],
    translation: str | None = None,
) -> MappingDecision:
    for c in initial_candidates:
        deps.seen[c["concept_id"]] = c
    listing = "\n".join(
        f"- {c['concept_id']}: {c['concept_name']} ({c.get('domain_id')}, {c.get('concept_class_id')})"
        for c in initial_candidates[:15]
    )
    vocabulary = (
        f"Source vocabulary (applies to every term in it): {deps.context}\n" if deps.context else ""
    )
    english = f"English rendering of the term: {translation}\n" if translation else ""
    prompt = (
        f"{vocabulary}Source term: {term}\n{english}\n"
        f"Initial candidates from vector search:\n{listing or '(none)'}"
    )
    settings = get_settings()
    # Raises UsageLimitExceeded at the limit; the refine step then keeps the first result
    result = await mapping_agent.run(
        prompt,
        deps=deps,
        model=model,
        usage_limits=UsageLimits(request_limit=settings.agent_request_limit),
        retries={"tools": 1, "output": settings.agent_output_retries},
    )
    return result.output
