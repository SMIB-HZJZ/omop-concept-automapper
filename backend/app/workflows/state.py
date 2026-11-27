"""Shared state flowing through mapping workflows."""

import operator
from typing import Annotated, Any, TypedDict

from app.llm.prompts import TERM_DESCRIPTION_LINE


class AutomapState(TypedDict, total=False):
    # Source concept
    source_id: int | None
    source_value: str
    source_name: str
    # Description of the term from the source list (e.g. when to use the code)
    source_description: str | None
    # Search inputs (nodes may rewrite `query`)
    query: str
    # English rendering of the source term (set by the `translate` step)
    translated_query: str | None
    # Drugs: structure provided with the source concept (ingredients, strengths, dose form)
    drug_structure: dict | None
    # Drugs: deterministic matches that tied; retrieval puts them first for the reranker
    structured_candidates: list[dict]
    # Extra context lines for the reranker (e.g. the parsed drug structure)
    notes: list[str]
    domains: list[str]
    # Target OMOP vocabularies (e.g. LOINC); empty: any. domain_mode applies to them too
    vocabularies: list[str]
    # restrict: only these domains (and vocabularies); prefer: these first, others allowed;
    # all: ignore domains and vocabularies
    domain_mode: str
    # Code systems of the source codes (e.g. ICD10), for the match_code step
    code_vocabularies: list[str]
    drug_specific: bool
    atc7_codes: list[str] | None
    vocabulary_id: str | None
    # Name and description of the source vocabulary, given to the LLM steps
    vocabulary_context: str | None
    # ISO 639-1 code set on the source vocabulary; None: the translate step detects it
    source_language: str | None
    threshold: int
    # Decision
    candidates: list[dict]
    selected: dict | None
    # Combined match: every concept to map together (selected is the first); empty otherwise
    selected_concepts: list[dict]
    confidence: int | None
    rationale: str | None
    # exact | broader | narrower | related (how the pick relates to the source term)
    match_type: str | None
    # Queries written by the rewrite_search step
    rewritten_queries: list[str]
    # Evidence for the final decision: every pick made so far (step, concept, confidence,
    # match type, rationale), and concepts the agent found with its own searches
    attempts: Annotated[list[dict], operator.add]
    agent_candidates: list[dict]
    method: str
    outcome: str  # accepted | rejected | no_candidates | no_match | error
    route: str
    error: str | None
    # Bookkeeping
    trace: Annotated[list[dict], operator.add]
    meta: Annotated[dict[str, Any], lambda a, b: {**(a or {}), **(b or {})}]


def term_notes(state: AutomapState) -> list[str]:
    """Context lines for the reranker: the term's description first, then the steps' notes."""
    description = state.get("source_description")
    lines = [TERM_DESCRIPTION_LINE.format(description=description).strip()] if description else []
    return [*lines, *(state.get("notes") or [])]


# Minimum confidence (1-10) for an automatic decision; see CONFIDENCE_SCALE in app.llm.prompts
DEFAULT_CONFIDENCE_THRESHOLD = 8
