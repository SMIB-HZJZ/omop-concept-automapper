"""Dependencies handed to every workflow node (injected so tests can supply fakes)."""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.core.config import Settings
from app.llm.schemas import MappingDecision
from app.llm.structured import CascadeReranker, EscalatingReranker, Reranker, Translator
from app.vector.store import VectorSearcher


@dataclass
class NodeContext:
    searcher: VectorSearcher
    reranker: Reranker | CascadeReranker | EscalatingReranker
    get_concepts: Callable[[list[int]], list[dict]]
    settings: Settings
    llm_model: str
    # Parents, defining attributes and synonyms of concepts (AI steps for unsure terms)
    get_concept_context: Callable[[list[int]], dict[int, dict]] | None = None
    # Full details of one concept (the agent's get_concept_details tool)
    get_details: Callable[[int], dict | None] | None = None
    # Used by the `translate` step; built from llm_model when not injected
    translator: Translator | None = None
    # Used by the `rewrite_search` step; built from llm_model when not injected
    rewriter: Any = None
    # Drug steps: LLM structure extraction and the deterministic drug_strength matcher
    drug_parser: Any = None
    # Source dose form term (any language) -> RxNorm dose form names
    dose_form_picker: Any = None
    # (structure, fuzzy, form_choice) -> DrugMatch
    drug_matcher: Callable[..., Any] | None = None
    # Text-based aids (app.services.lexical); None in tests without a database
    lexical: Any = None
    # Runs the Pydantic AI mapping agent: (term, deps, initial candidates) -> decision
    run_agent: Callable[..., Awaitable[MappingDecision]] | None = None
    # Pydantic AI model override (tests pass TestModel/FunctionModel here)
    agent_model: Any = None
    # Told when a step starts (state, node id, step type, detail), for live progress in the UI.
    # The detail is a ready label for events inside a step (escalation, agent searches).
    on_step: Callable[[dict, str | None, str, str | None], None] | None = None
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("workflow"))
    extras: dict[str, Any] = field(default_factory=dict)

    def report(self, state: dict, node_id: str | None, uses: str, detail: str | None = None):
        """Progress is best effort: a failing listener never breaks a mapping."""
        if self.on_step is None:
            return
        try:
            self.on_step(state, node_id, uses, detail)
        except Exception:
            self.logger.debug("Step listener failed", exc_info=True)
