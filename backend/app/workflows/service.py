"""Running pipelines: building the node context and invoking compiled graphs."""

import logging
from pathlib import Path

from sqlmodel import Session

from app.core.cache import cached
from app.core.config import get_settings
from app.core.db import get_engine
from app.llm.drug_parser import DoseFormPicker, DrugParser
from app.llm.embeddings import Embedder
from app.llm.structured import CascadeReranker, EscalatingReranker, Reranker, Translator
from app.services import concepts as concept_service
from app.services import drug_matching, lexical
from app.services.settings_store import EffectiveConfig
from app.vector.store import PgVectorStore, SearchFilters
from app.workflows.builder import get_pipeline, load_definitions
from app.workflows.context import NodeContext
from app.workflows.plugins import load_errors, load_plugins
from app.workflows.registry import all_steps
from app.workflows.state import AutomapState

logger = logging.getLogger(__name__)


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


def _concept_context(concept_ids: list[int]) -> dict[int, dict]:
    with Session(get_engine()) as session:
        return concept_service.candidate_context(session, concept_ids)


def _agent_details(concept_id: int) -> dict | None:
    with Session(get_engine()) as session:
        return concept_service.agent_details(session, concept_id)


def _brand_in_name(name: str) -> str | None:
    with Session(get_engine()) as session:
        return drug_matching.brand_in_name(session, name)


def _match_drug(structure, fuzzy: bool = False, form_choice: list[str] | None = None):
    with Session(get_engine()) as session:
        return drug_matching.match_structure(
            session, structure, fuzzy=fuzzy, form_choice=form_choice
        )


@cached("vocab")
def _drug_vocabulary_info(session: Session) -> dict:
    return {
        "dose_forms": drug_matching.dose_form_names(session),
        "has_drug_strength": drug_matching.has_drug_strength(session),
    }


def _drug_parser(llm_model: str) -> DrugParser | None:
    """None when drug_strength is not loaded (the drug step then falls back right away)."""
    with Session(get_engine()) as session:
        info = _drug_vocabulary_info(session)
    if not info["has_drug_strength"]:
        return None
    return DrugParser(llm_model, info["dose_forms"], effort=get_settings().llm_reasoning_effort)


def _dose_form_picker(llm_model: str) -> DoseFormPicker | None:
    with Session(get_engine()) as session:
        info = _drug_vocabulary_info(session)
    if not info["has_drug_strength"]:
        return None
    return DoseFormPicker(llm_model, info["dose_forms"], effort=get_settings().llm_reasoning_effort)


def default_pipeline(drug_specific: bool) -> str:
    """AUTOMAP_DRUG_PIPELINE for drug vocabularies once drug_strength is loaded, else AUTOMAP_PIPELINE."""
    settings = get_settings()
    if drug_specific:
        with Session(get_engine()) as session:
            if _drug_vocabulary_info(session)["has_drug_strength"]:
                return settings.automap_drug_pipeline
    return settings.automap_pipeline


def build_reranker(llm_model: str) -> Reranker | CascadeReranker | EscalatingReranker:
    """The configured LLM (or the RERANK_VOTERS cascade), escalating unsure terms to
    RERANK_ESCALATE_MODEL (at RERANK_ESCALATE_EFFORT) when that is set."""
    settings = get_settings()
    effort = settings.llm_reasoning_effort or None
    voters = settings.rerank_voter_list
    first: Reranker | CascadeReranker
    if len(voters) >= 2:
        judge = settings.rerank_judge.strip() or llm_model
        first = CascadeReranker(
            [Reranker(m) for m in voters],
            Reranker(judge, effort=effort if judge == llm_model else None),
        )
    else:
        if voters:
            logger.warning("RERANK_VOTERS needs at least two models; using %s alone", llm_model)
        first = Reranker(llm_model, effort=effort)
    stronger = settings.rerank_escalate_model.strip()
    stronger_effort = settings.rerank_escalate_effort or None
    if (
        not stronger
        or settings.rerank_escalate_below <= 0
        # Escalating to the same model at the same effort would only repeat the answer
        or (stronger == llm_model and stronger_effort == effort and len(voters) < 2)
    ):
        return first
    return EscalatingReranker(
        first,
        Reranker(stronger, effort=stronger_effort),
        settings.rerank_escalate_below,
        settings.rerank_escalate_from,
    )


def build_context(cfg: EffectiveConfig) -> NodeContext:
    drug_parser = _drug_parser(cfg.llm_model)
    return NodeContext(
        searcher=SessionSearcher(
            cfg.collection_name,
            Embedder(cfg.embedding_model, cfg.embedding_dims, cfg.embedding_task_types),
        ),
        reranker=build_reranker(cfg.llm_model),
        translator=Translator(cfg.llm_model, effort=get_settings().llm_reasoning_effort),
        drug_parser=drug_parser,
        dose_form_picker=_dose_form_picker(cfg.llm_model) if drug_parser is not None else None,
        # Both need drug_strength; without it the drug step falls back immediately
        drug_matcher=_match_drug if drug_parser is not None else None,
        brand_finder=_brand_in_name if drug_parser is not None else None,
        lexical=lexical,
        get_concepts=_get_concepts,
        get_concept_context=_concept_context,
        get_details=_agent_details,
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
    drug_structure: dict | None = None,
    domain_mode: str = "restrict",
    code_vocabularies: list[str] | None = None,
    vocabulary_context: str | None = None,
    source_language: str | None = None,
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
        translated_query=None,
        drug_structure=drug_structure,
        domain_mode=domain_mode,
        code_vocabularies=code_vocabularies or [],
        vocabulary_context=vocabulary_context,
        source_language=source_language,
        match_type=None,
        structured_candidates=[],
        selected_concepts=[],
        notes=[],
        outcome="pending",
        trace=[],
        meta={},
    )


async def run_pipeline(
    state: AutomapState,
    ctx: NodeContext,
    pipeline: str | None = None,
    *,
    compiled: tuple | None = None,
) -> AutomapState:
    """Run one concept through a pipeline. Pass ``compiled`` (from ``get_pipeline``) when running
    many concepts, so the definitions are not re-read for each one."""
    graph, definition = compiled or get_pipeline(pipeline)
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
    drug_default = get_settings().automap_drug_pipeline
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
            "is_drug_default": d.name == drug_default,
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
        "pipelines": sorted(
            pipelines, key=lambda p: (not p["is_default"], not p["is_drug_default"], p["name"])
        ),
        "nodes": nodes,
        "errors": {**load_errors, **errors},
    }
