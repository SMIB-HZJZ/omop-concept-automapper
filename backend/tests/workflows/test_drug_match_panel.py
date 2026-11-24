"""The drug step for one term in the mapping panel: status and search text it reports, the
drug-match endpoint, and the Map tab search with several queries and the ATC7 fallback."""

import dataclasses

from app.llm.schemas import DrugComponent, DrugStructure
from app.workflows.nodes.drug import MatchDrugParams, match_drug, search_text
from app.workflows.nodes.retrieve import excluded_drug_classes
from tests.conftest import import_source_csv
from tests.workflows.drug_fixtures import drug_ctx, drug_state

DRUG_CSV = (
    b"source_value,source_concept_name,freq,ingredient,amount_value,amount_unit,"
    b"numerator_value,numerator_unit,denominator_value,denominator_unit,dose_form,"
    b"quantity_value,quantity_unit\n"
    b"P1,APAP 500 mg tbl,5,APAP,500,mg,,,,,Oral Tablet,,\n"
    b"P2,Diazepam 10 mg/2 ml amp,3,diazepam,,,10,mg,2,ml,Injectable Solution,2,ml\n"
    b"P3,Something else,1,,,,,,,,,,\n"
)


def test_search_text_reads_like_an_rxnorm_name():
    structure = DrugStructure(
        components=[
            DrugComponent(
                ingredient="diazepam",
                numerator_value=10,
                numerator_unit="mg",
                denominator_value=2,
                denominator_unit="mL",
            )
        ],
        dose_form="Injectable Solution",
        brand_name="Normabel",
        quantity_value=2,
        quantity_unit="mL",
    )
    assert search_text(structure) == "2 ML diazepam 5 MG/ML Injectable Solution [Normabel]"
    combo = DrugStructure(
        components=[
            DrugComponent(ingredient="amoxicillin", amount_value=875, amount_unit="mg"),
            DrugComponent(ingredient="clavulanate", amount_value=125, amount_unit="mg"),
        ],
        dose_form="Oral Tablet",
    )
    assert search_text(combo) == "amoxicillin 875 MG / clavulanate 125 MG Oral Tablet"


async def test_step_reports_status_and_rxnorm_search_text(drug_vocabulary):
    structure = {
        "components": [{"ingredient": "APAP", "amount_value": 500, "amount_unit": "mg"}],
        "dose_form": "oral tablet",
    }
    route, update = await match_drug(
        drug_state("APAP 500 mg", structure), drug_ctx(), MatchDrugParams()
    )
    assert route == "matched"
    meta = update["meta"]
    assert meta["drug_status"] == "exact" and meta["drug_origin"] == "provided"
    assert meta["drug_level"] == "clinical_drug"
    # The synonym and the dose form are written with their RxNorm names
    assert meta["drug_search_text"] == "acetaminophen 500 MG Oral Tablet"

    no_drug = {"components": [{"ingredient": "acetaminophen", "amount_value": 650}]}
    route, update = await match_drug(
        drug_state("x", no_drug), drug_ctx(), MatchDrugParams(parse_free_text=False)
    )
    assert route == "fallback" and update["meta"]["drug_status"] == "none"


def test_drug_match_endpoint(client, admin_headers, user_headers, drug_vocabulary):
    import_source_csv(client, admin_headers, 931, DRUG_CSV, is_drug="true")
    items = client.get(
        "/api/source-vocabularies/931/unmapped", params={"view": "all"}, headers=user_headers
    ).json()["items"]
    by = {i["source_value"]: i for i in items}
    # The list carries the provided structure, free text has none
    assert by["P1"]["drug_structure"]["components"][0]["ingredient"] == "APAP"
    assert by["P3"]["drug_structure"] is None

    body = client.post(
        f"/api/source-concepts/{by['P1']['source_id']}/drug-match", json={}, headers=user_headers
    ).json()
    assert body["status"] == "exact" and body["origin"] == "provided"
    assert [c["concept_name"] for c in body["candidates"]] == ["acetaminophen 500 MG Oral Tablet"]
    assert body["search_text"] == "acetaminophen 500 MG Oral Tablet"
    assert "Branded Drug" in body["exclude_classes"] and body["brand_in_name"] is None
    assert any("APAP → acetaminophen" in r for r in body["resolved"])

    body = client.post(
        f"/api/source-concepts/{by['P2']['source_id']}/drug-match", json={}, headers=user_headers
    ).json()
    assert body["status"] == "exact" and body["level"] == "quant_clinical_drug"

    # An edited structure is matched as sent (origin edited), not replaced by a parse
    edited = {
        "structure": {
            "components": [
                {"ingredient": "acetaminophen", "amount_value": 650, "amount_unit": "mg"}
            ],
            "dose_form": "Oral Tablet",
        }
    }
    body = client.post(
        f"/api/source-concepts/{by['P1']['source_id']}/drug-match",
        json=edited,
        headers=user_headers,
    ).json()
    assert body["origin"] == "edited" and body["status"] == "none" and body["candidates"] == []

    missing = client.post("/api/source-concepts/999999/drug-match", json={}, headers=user_headers)
    assert missing.status_code == 404


def test_search_with_extra_queries_and_atc7_fallback(client, user_headers, embedded):
    one = client.post(
        "/api/search", json={"query": "metformin", "k": 5}, headers=user_headers
    ).json()
    two = client.post(
        "/api/search",
        json={"query": "metformin", "extra_queries": ["metformin", "hypertension"], "k": 5},
        headers=user_headers,
    ).json()
    assert two["method"].endswith("2 queries")  # duplicates are searched once
    # Best score per concept across the queries, ranked again
    scores = [r["score"] for r in two["results"]]
    assert scores == sorted(scores, reverse=True) and scores[0] >= one["results"][0]["score"]
    assert [r["rank"] for r in two["results"]] == list(range(1, len(two["results"]) + 1))
    assert any(
        r["concept_id"] not in {x["concept_id"] for x in one["results"]} for r in two["results"]
    )

    strict = client.post(
        "/api/search",
        json={"query": "metformin", "atc7_codes": ["Z99ZZ99"], "k": 5},
        headers=user_headers,
    ).json()
    assert strict["results"] == []
    relaxed = client.post(
        "/api/search",
        json={"query": "metformin", "atc7_codes": ["Z99ZZ99"], "atc7_fallback": True, "k": 5},
        headers=user_headers,
    ).json()
    assert relaxed["results"] and relaxed["method"].startswith("No ATC7 match")


def test_search_can_leave_branded_drugs_out(client, user_headers, embedded):
    body = {"query": "metformin oral tablet", "k": 20, "domains": ["Drug"]}
    found = client.post("/api/search", json=body, headers=user_headers).json()["results"]
    assert "Branded Drug Form" in {r["concept_class_id"] for r in found}
    found = client.post(
        "/api/search",
        json={**body, "exclude_concept_classes": ["Branded Drug Form"]},
        headers=user_headers,
    ).json()["results"]
    assert found and "Branded Drug Form" not in {r["concept_class_id"] for r in found}


def test_brand_written_in_the_name(drug_vocabulary):
    from sqlmodel import Session

    from app.core.db import get_engine
    from app.services.drug_matching import brand_in_name

    with Session(get_engine()) as session:
        assert brand_in_name(session, "Normabel 10 mg/2 ml amp.") == "Normabel"
        assert brand_in_name(session, "Diazepam 10 mg/2 ml amp.") is None
        assert brand_in_name(session, "") is None


def test_excluded_classes_follow_the_drug_step():
    from app.services.drug_matching import BRANDED_CLASSES

    state = {"drug_specific": True, "meta": {"drug_exclude_branded": True}}
    assert excluded_drug_classes(state) == BRANDED_CLASSES
    assert excluded_drug_classes({**state, "meta": {}}) == ()  # nothing decided: keep them
    assert excluded_drug_classes({**state, "drug_specific": False}) == ()


async def test_drug_step_decides_about_branded_drugs(drug_vocabulary):
    apap = {
        "components": [{"ingredient": "APAP", "amount_value": 500, "amount_unit": "mg"}],
        "dose_form": "Oral Tablet",
    }
    _, update = await match_drug(drug_state("APAP 500 mg", apap), drug_ctx(), MatchDrugParams())
    assert update["meta"]["drug_exclude_branded"] is True
    # Turned off in the pipeline
    _, update = await match_drug(
        drug_state("APAP 500 mg", apap),
        drug_ctx(),
        MatchDrugParams(skip_branded_without_brand=False),
    )
    assert update["meta"]["drug_exclude_branded"] is False
    # A brand written in the name (the brand_finder answers as the vocabulary lookup would)
    ctx = dataclasses.replace(drug_ctx(), brand_finder=lambda name: "Lekadol")
    _, update = await match_drug(drug_state("Lekadol 500 mg", apap), ctx, MatchDrugParams())
    assert update["meta"]["drug_exclude_branded"] is False
    assert update["meta"]["drug_brand_in_name"] == "Lekadol"


async def test_retrieve_searches_the_rxnorm_style_text_too():
    from app.workflows.nodes.retrieve import RetrieveParams, retrieve
    from tests.workflows.helpers import FakeSearcher, make_context

    searcher = FakeSearcher()
    state = drug_state("Paracetamol 500 mg tablete")
    state["meta"] = {"drug_search_text": "acetaminophen 500 MG Oral Tablet"}
    await retrieve(state, make_context(None, searcher=searcher), RetrieveParams())
    assert {q for q, *_ in searcher.calls} == {
        "Paracetamol 500 mg tablete",
        "acetaminophen 500 MG Oral Tablet",
    }
    searcher.calls.clear()
    await retrieve(
        state, make_context(None, searcher=searcher), RetrieveParams(drug_search_text=False)
    )
    assert {q for q, *_ in searcher.calls} == {"Paracetamol 500 mg tablete"}


def test_concentration_without_volume_is_approximate_on_quant_drugs(drug_vocabulary):
    """5 mg/mL could be a 2 mL ampoule or a 10 mL vial: with only the 2 mL Quant drug left, the
    match goes to the reranker instead of being accepted."""
    from sqlalchemy import text
    from sqlmodel import Session

    from app.core import cache
    from app.core.db import get_engine
    from app.services.drug_matching import match_structure

    structure = DrugStructure(
        components=[
            DrugComponent(
                ingredient="diazepam", numerator_value=5, numerator_unit="mg", denominator_unit="mL"
            )
        ],
        dose_form="Injectable Solution",
    )
    hide = "UPDATE drug_strength SET invalid_reason = :r WHERE drug_concept_id IN (9038, 9046)"
    with get_engine().begin() as conn:
        conn.execute(text(hide), {"r": "D"})
    cache.invalidate("vocab")
    try:
        with Session(get_engine()) as session:
            match = match_structure(session, structure)
        assert match.concept_ids == [9037] and match.level == "quant_clinical_drug"
        assert match.approximate and "volume" in match.approximate[0]
    finally:
        with get_engine().begin() as conn:
            conn.execute(text(hide), {"r": None})
        cache.invalidate("vocab")
    # With the volume given it is exact again
    with Session(get_engine()) as session:
        exact = match_structure(
            session, structure.model_copy(update={"quantity_value": 2, "quantity_unit": "mL"})
        )
    assert exact.concept_ids == [9037] and not exact.approximate


class UsualFormPicker:
    """Knows only the usual form for a listed one no product uses (the ``unused`` question)."""

    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    async def pick(self, form, product=None, unused=False):
        self.calls.append((form, unused))
        return self.answer if unused else []


async def test_usual_form_question_decides_the_match(drug_vocabulary):
    """ "Tablet" is an RxNorm form, but no acetaminophen product uses it: the step asks for the
    usual form and matches with that; without an answer there is no match."""
    tablet = {
        "components": [{"ingredient": "acetaminophen", "amount_value": 500, "amount_unit": "mg"}],
        "dose_form": "Tablet",
    }
    picker = UsualFormPicker(["Oral Tablet"])
    route, update = await match_drug(
        drug_state("acetaminophen 500 mg tablet", tablet),
        drug_ctx(picker=picker),
        MatchDrugParams(parse_free_text=False),
    )
    assert picker.calls == [("Tablet", True)]
    assert route == "matched" and update["selected"]["concept_id"] == 3

    silent = UsualFormPicker([])
    route, update = await match_drug(
        drug_state("acetaminophen 500 mg tablet", tablet),
        drug_ctx(picker=silent),
        MatchDrugParams(parse_free_text=False),
    )
    assert silent.calls == [("Tablet", True)]
    assert route == "fallback" and update["meta"]["drug_status"] == "none"
