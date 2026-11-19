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
    get_ancestors: Callable[[int, int], list[dict]]
    settings: Settings
    llm_model: str
    # Used by the `translate` step; built from llm_model when not injected
    translator: Translator | None = None
    # Drug steps: LLM structure extraction and the deterministic drug_strength matcher
    drug_parser: Any = None
    drug_matcher: Callable[[Any], Any] | None = None
    # Text-based aids (app.services.lexical); None in tests without a database
    lexical: Any = None
    # Runs the Pydantic AI mapping agent: (term, deps, initial candidates) -> decision
    run_agent: Callable[..., Awaitable[MappingDecision]] | None = None
    # Pydantic AI model override (tests pass TestModel/FunctionModel here)
    agent_model: Any = None
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("workflow"))
    extras: dict[str, Any] = field(default_factory=dict)
