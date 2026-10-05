"""Shared state flowing through mapping workflows."""

import operator
from typing import Annotated, Any, TypedDict


class AutomapState(TypedDict, total=False):
    # Source concept
    source_id: int | None
    source_value: str
    source_name: str
    # Search inputs (nodes may rewrite `query`)
    query: str
    domains: list[str]
    drug_specific: bool
    atc7_codes: list[str] | None
    vocabulary_id: str | None
    threshold: int
    # Decision
    candidates: list[dict]
    selected: dict | None
    confidence: int | None
    rationale: str | None
    method: str
    outcome: str  # accepted | rejected | no_candidates | no_match | error
    route: str
    error: str | None
    # Bookkeeping
    trace: Annotated[list[dict], operator.add]
    meta: Annotated[dict[str, Any], lambda a, b: {**(a or {}), **(b or {})}]
