"""Target vocabularies follow the domain mode: restrict filters and drops, prefer filters and adds a
few others, all ignores them."""

import asyncio
from types import SimpleNamespace

from app.llm.agents.mapping_agent import AgentDeps, search_concepts
from app.schemas.api import AutomapRequest
from app.workflows import scope
from app.workflows.nodes.retrieve import RetrieveParams, retrieve
from app.workflows.service import initial_state
from tests.api.test_text_matching import _automap, _suggestions
from tests.conftest import import_source_csv
from tests.workflows.helpers import make_context

CONCEPTS = [
    {"concept_id": 1, "concept_name": "Glucose in serum", "domain_id": "Measurement",
     "vocabulary_id": "LOINC", "concept_class_id": "Lab Test", "standard_concept": "S"},
    {"concept_id": 2, "concept_name": "Glucose measurement", "domain_id": "Measurement",
     "vocabulary_id": "SNOMED", "concept_class_id": "Procedure", "standard_concept": "S"},
    {"concept_id": 3, "concept_name": "Glucose", "domain_id": "Drug",
     "vocabulary_id": "RxNorm", "concept_class_id": "Ingredient", "standard_concept": "S"},
]  # fmt: skip


class VocabularySearcher:
    def __init__(self):
        self.filters = []

    def search(self, query, k, filters):
        self.filters.append(filters)
        vocabularies = filters.vocabulary_ids()
        found = [
            c
            for c in CONCEPTS
            if (not filters.domains or c["domain_id"] in filters.domains)
            and (not vocabularies or c["vocabulary_id"] in vocabularies)
        ]
        return [{**c, "score": 0.9 - i / 10, "rank": i + 1} for i, c in enumerate(found)]


def _state(mode: str, domains=("Measurement",), vocabularies=("LOINC",)):
    return initial_state(
        source_id=1,
        source_value="G",
        source_name="glukoza",
        domains=list(domains),
        vocabularies=list(vocabularies),
        drug_specific=False,
        threshold=8,
        domain_mode=mode,
    )


def test_scope_helpers():
    restrict = _state("restrict")
    assert scope.targets(restrict) == (["Measurement"], ["LOINC"])
    assert scope.in_scope(restrict, CONCEPTS) == CONCEPTS[:1]
    assert scope.describe(restrict) == "domains Measurement and vocabularies LOINC"
    prefer = _state("prefer")
    assert scope.in_scope(prefer, CONCEPTS) == CONCEPTS and scope.preferred(prefer)
    assert scope.targets(_state("all")) == ([], [])
    # No vocabulary chosen: no vocabulary filter
    assert scope.in_scope(_state("restrict", vocabularies=()), CONCEPTS) == CONCEPTS[:2]


def test_retrieve_restricts_or_prefers_target_vocabularies():
    searcher = VocabularySearcher()
    ctx = make_context(None, searcher=searcher)
    result = asyncio.run(retrieve(_state("restrict"), ctx, RetrieveParams()))
    assert [c["concept_id"] for c in result["candidates"]] == [1]
    assert searcher.filters[0].vocabularies == ["LOINC"]

    result = asyncio.run(retrieve(_state("prefer"), ctx, RetrieveParams()))
    # LOINC first, then a few from outside the preferences (other vocabulary or domain)
    assert [c["concept_id"] for c in result["candidates"]] == [1, 2, 3]
    assert "Preferred domains Measurement and vocabularies LOINC" in result["notes"][-1]

    result = asyncio.run(retrieve(_state("all"), ctx, RetrieveParams()))
    assert searcher.filters[-1].vocabularies == [] and searcher.filters[-1].domains == []


def test_agent_searches_stay_inside_restricted_vocabularies():
    searcher = VocabularySearcher()

    def deps(restrict: bool) -> AgentDeps:
        return AgentDeps(
            searcher=searcher,
            get_concepts=lambda ids: [],
            get_details=lambda cid: None,
            domains=["Measurement"],
            vocabularies=["LOINC"],
            restrict=restrict,
            drug_specific=False,
        )

    run = SimpleNamespace(deps=deps(True))
    found = asyncio.run(search_concepts(run, "glucose", domains=["Drug"], vocabularies=["RxNorm"]))
    assert [c["concept_id"] for c in found] == [1]
    run = SimpleNamespace(deps=deps(False))
    found = asyncio.run(search_concepts(run, "glucose", domains=["Drug"], vocabularies=["RxNorm"]))
    assert [c["concept_id"] for c in found] == [3]


def test_all_domains_drops_target_vocabularies():
    request = AutomapRequest(domain_mode="all", vocabularies=["LOINC"])
    assert request.vocabularies == []


def test_restricted_vocabularies_reject_text_matches_elsewhere(
    client, user_headers, embedded, fake_reranker, admin_headers
):
    # "Headache" is an exact SNOMED name: not accepted when only LOINC is allowed
    import_source_csv(client, admin_headers, 64, b"source_value,source_concept_name\nX,Headache\n")
    task = _automap(
        client,
        admin_headers,
        64,
        domains=["Condition"],
        vocabularies=["LOINC"],
        domain_mode="restrict",
    )
    assert task["status"] == "SUCCESS", task
    found = _suggestions(client, user_headers, 64).get("Headache", {})
    assert found.get("method") != "auto_exact_name"
