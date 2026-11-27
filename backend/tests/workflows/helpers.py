"""Fakes for running workflows without a database or an LLM provider."""

import logging
import re

from app.core.config import get_settings
from app.workflows.context import NodeContext

CONCEPTS = {
    1: {
        "concept_id": 1,
        "concept_name": "metformin 500 MG Oral Tablet",
        "domain_id": "Drug",
        "concept_class_id": "Clinical Drug",
        "vocabulary_id": "RxNorm",
        "concept_code": "861007",
        "standard_concept": "S",
        "atc7_codes": ["A10BA02"],
    },
    2: {
        "concept_id": 2,
        "concept_name": "metformin 850 MG Oral Tablet",
        "domain_id": "Drug",
        "concept_class_id": "Clinical Drug",
        "vocabulary_id": "RxNorm",
        "concept_code": "861010",
        "standard_concept": "S",
        "atc7_codes": ["A10BA02"],
    },
    3: {
        "concept_id": 3,
        "concept_name": "acetaminophen 500 MG Oral Tablet",
        "domain_id": "Drug",
        "concept_class_id": "Clinical Drug",
        "vocabulary_id": "RxNorm",
        "concept_code": "198440",
        "standard_concept": "S",
        "atc7_codes": ["N02BE01"],
    },
    201: {
        "concept_id": 201,
        "concept_name": "Type 2 diabetes mellitus",
        "domain_id": "Condition",
        "concept_class_id": "Clinical Finding",
        "vocabulary_id": "SNOMED",
        "concept_code": "44054006",
        "standard_concept": "S",
        "atc7_codes": None,
    },
}


class FakeSearcher:
    def __init__(self):
        self.calls = []

    def search(self, query, k, filters):
        self.calls.append((query, k, tuple(filters.domains), tuple(filters.atc7_codes)))
        words = set(re.findall(r"[a-z0-9]+", query.lower()))
        results = []
        for c in CONCEPTS.values():
            if filters.domains and c["domain_id"] not in filters.domains:
                continue
            if filters.atc7_codes and not set(filters.atc7_codes) & set(c["atc7_codes"] or []):
                continue
            overlap = len(words & set(re.findall(r"[a-z0-9]+", c["concept_name"].lower())))
            if overlap:
                results.append({**c, "score": overlap / 10})
        results.sort(key=lambda r: -r["score"])
        return [{**r, "rank": i + 1} for i, r in enumerate(results[:k])]


class IdentityTranslator:
    """The default pipeline translates; test terms count as English already."""

    async def translate(self, term, context=None, **kwargs):
        from app.llm.structured import TranslationResult

        return TranslationResult(term, "en")


async def no_match_agent(model, term, deps, candidates, translation=None):
    """Every pipeline ends with the refine agent for unsure terms; by default it finds nothing."""
    from app.llm.schemas import MappingDecision

    return MappingDecision(concept_id=None, rationale="test agent: no match")


class NoRewrites:
    """Unsure terms get rewritten searches; by default there are none (no real LLM call)."""

    async def rewrite(self, term, **kwargs):
        return []


class NoFinalPick:
    """The final decision on all evidence; by default it finds nothing (earlier result stands)."""

    async def rerank(self, term, candidates, drug_specific, **kwargs):
        from app.llm.structured import RerankResult

        return RerankResult(None, None)


def make_context(reranker, **kwargs) -> NodeContext:
    kwargs.setdefault("translator", IdentityTranslator())
    kwargs.setdefault("extras", {}).setdefault("final_reranker", NoFinalPick())
    kwargs.setdefault("rewriter", NoRewrites())
    if "agent_model" not in kwargs:
        kwargs.setdefault("run_agent", no_match_agent)
    return NodeContext(
        searcher=kwargs.pop("searcher", FakeSearcher()),
        reranker=reranker,
        get_concepts=lambda ids: [CONCEPTS[i] for i in ids if i in CONCEPTS],
        settings=get_settings(),
        llm_model="test/chat",
        logger=logging.getLogger("test"),
        **kwargs,
    )
