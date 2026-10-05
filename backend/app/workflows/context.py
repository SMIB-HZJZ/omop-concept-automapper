"""Dependencies handed to every workflow node (injected so tests can supply fakes)."""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.core.config import Settings
from app.llm.schemas import MappingDecision
from app.llm.structured import Reranker
from app.vector.store import VectorSearcher


@dataclass
class NodeContext:
    searcher: VectorSearcher
    reranker: Reranker
    get_concepts: Callable[[list[int]], list[dict]]
    get_ancestors: Callable[[int, int], list[dict]]
    settings: Settings
    llm_model: str
    # Runs the Pydantic AI mapping agent: (term, deps, initial candidates) -> decision
    run_agent: Callable[..., Awaitable[MappingDecision]] | None = None
    # Pydantic AI model override (tests pass TestModel/FunctionModel here)
    agent_model: Any = None
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("workflow"))
    extras: dict[str, Any] = field(default_factory=dict)
