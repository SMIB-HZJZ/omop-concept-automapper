"""Concept context for the AI steps on unsure terms: parents, attributes, synonyms."""

from sqlmodel import Session

from app.core.db import get_engine
from app.llm.structured import attribute_name, format_candidates
from app.services import concepts


def test_candidate_context_and_agent_details(loaded_vocabulary):
    with Session(get_engine()) as session:
        context = concepts.candidate_context(session, [1, 4, 201])
        parent = concepts.get_concepts(session, [101])[0]["concept_name"]
        ingredient = concepts.get_concepts(session, [9])[0]["concept_name"]
        details = concepts.agent_details(session, 4)
    assert context[1]["parents"] == [parent]
    assert context[201]["synonyms"][:1] == ["Diabetes mellitus type 2"]
    # RxNorm ingredient links count as attributes; "Maps to"/"Mapped from" do not
    assert context[4]["attributes"] == [("RxNorm has ing", ingredient)]
    assert context[201]["attributes"] == []
    assert details["concept"]["concept_id"] == 4
    assert [r["relationship_id"] for r in details["attributes"]] == ["RxNorm has ing"]
    assert "children" in details and "child_count" in details


def test_candidates_are_listed_with_their_context():
    text = format_candidates(
        [
            {
                "concept_name": "Osteonecrosis",
                "concept_class_id": "Disorder",
                "domain_id": "Condition",
                "vocabulary_id": "SNOMED",
                "context": {
                    "parents": ["Necrosis of bone"],
                    "attributes": [("Has finding site", "Bone structure")],
                    "synonyms": ["Avascular necrosis"],
                },
            },
            {"concept_name": "Fracture", "domain_id": "Condition"},
        ]
    )
    assert text.splitlines() == [
        "0: Osteonecrosis [Disorder | Condition | SNOMED]",
        "   is a: Necrosis of bone",
        "   finding site: Bone structure",
        "   also: Avascular necrosis",
        "1: Fracture [Condition]",
    ]
    assert attribute_name("RxNorm has ing") == "ingredient"
    assert attribute_name("Has asso morph") == "associated morphology"
