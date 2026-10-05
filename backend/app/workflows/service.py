"""Running pipelines: building the node context and invoking compiled graphs."""

from pathlib import Path

from sqlmodel import Session

from app.core.config import get_settings
from app.core.db import get_engine
from app.llm.embeddings import Embedder
from app.llm.structured import Reranker
from app.services import concepts as concept_service
from app.services.settings_store import EffectiveConfig
from app.vector.store import PgVectorStore, SearchFilters
from app.workflows.builder import get_pipeline, load_definitions
from app.workflows.context import NodeContext
from app.workflows.plugins import load_errors, load_plugins
from app.workflows.registry import all_steps
from app.workflows.state import AutomapState


class SessionSearcher:
    """Thread-safe searcher: opens a short-lived session for each search."""

    def __init__(self, collection_name: str, embedder: Embedder):
        self.collection_name = collection_name
        self.embedder = embedder

    def search(self, query: str, k: int, filters: SearchFilters) -> list[dict]:
        with Session(get_engine()) as session:
            return PgVectorStore(session, self.collection_name, self.embedder).search(
                query, k, filters
            )


def _get_concepts(ids: list[int]) -> list[dict]:
    with Session(get_engine()) as session:
        return concept_service.get_concepts(session, ids)


def _get_ancestors(concept_id: int, levels: int) -> list[dict]:
    with Session(get_engine()) as session:
        return concept_service.get_ancestors(session, concept_id, levels)


def build_context(cfg: EffectiveConfig) -> NodeContext:
    return NodeContext(
        searcher=SessionSearcher(
            cfg.collection_name, Embedder(cfg.embedding_model, cfg.embedding_dims)
        ),
        reranker=Reranker(cfg.llm_model),
        get_concepts=_get_concepts,
        get_ancestors=_get_ancestors,
        settings=get_settings(),
        llm_model=cfg.llm_model,
    )


def initial_state(
    *,
    source_id: int | None,
    source_value: str,
    source_name: str,
    query: str | None = None,
    domains: list[str],
    drug_specific: bool,
    threshold: int,
    atc7_codes: list[str] | None = None,
    vocabulary_id: str | None = None,
) -> AutomapState:
    return AutomapState(
        source_id=source_id,
        source_value=source_value or "",
        source_name=source_name,
        query=query or source_name,
        domains=domains,
        drug_specific=drug_specific,
        threshold=threshold,
        atc7_codes=atc7_codes,
        vocabulary_id=vocabulary_id,
        candidates=[],
        selected=None,
        confidence=None,
        rationale=None,
        outcome="pending",
        trace=[],
        meta={},
    )


async def run_pipeline(
    state: AutomapState, ctx: NodeContext, pipeline: str | None = None
) -> AutomapState:
    graph, definition = get_pipeline(pipeline)
    result = await graph.ainvoke(state, context=ctx)
    result.setdefault("meta", {})
    result["meta"] = {**result["meta"], "pipeline": definition.name}
    if result.get("outcome") in (None, "pending", "selected"):
        # A pipeline without a gate: a selection counts as accepted
        result["outcome"] = "accepted" if result.get("selected") else "no_match"
    return result


def catalog() -> dict:
    load_plugins()
    definitions, errors = load_definitions()
    default = get_settings().automap_pipeline
    pipelines = [
        {
            "name": d.name,
            "description": d.description,
            "entry": d.entry,
            "nodes": {k: v.model_dump(exclude_none=True) for k, v in d.nodes.items()},
            "edges": [list(e) for e in d.edges],
            "source": "builtin"
            if Path(path).parent.name == "definitions" and "app/workflows" in path
            else path,
            "is_default": d.name == default,
        }
        for d, path in definitions.values()
    ]
    nodes = [
        {
            "name": s.name,
            "kind": s.kind,
            "description": s.description,
            "routes": sorted(s.routes) if s.kind == "router" else None,
            "plugin": s.plugin,
        }
        for s in all_steps()
    ]
    return {
        "pipelines": sorted(pipelines, key=lambda p: (not p["is_default"], p["name"])),
        "nodes": nodes,
        "errors": {**load_errors, **errors},
    }
