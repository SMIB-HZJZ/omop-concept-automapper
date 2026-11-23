"""Deterministic drug mapping on drug_strength: units, CSV structure, matcher and pipeline."""

import pytest
from sqlmodel import Session

from app.core.db import get_engine
from app.llm.schemas import DrugComponent, DrugStructure, ParsedDrug
from app.services import drug_matching
from app.services.drug_units import close, concentration, quantity
from app.services.source_import import SourceImportError, parse_drug_structure
from tests.workflows.drug_fixtures import FakeParser, drug_ctx, drug_state


def _structure(*components, form=None) -> DrugStructure:
    return DrugStructure(components=[DrugComponent(**c) for c in components], dose_form=form)


def _match(structure):
    with Session(get_engine()) as session:
        return drug_matching.match_structure(session, structure)


# --------------------------------------------------------------------------- units


def test_units_normalise():
    assert quantity(0.5, "g") == quantity(500, "mg")
    assert quantity(500, "mcg") == quantity(0.5, "mg")
    assert quantity(5, "I.U.").dimension == "iu"
    # 500 mcg / 5 mL = 0.1 mg/mL
    assert close(concentration(500, "mcg", 5, "ml")[0], 0.1)
    # 1 % = 10 mg per g (creams) or per mL
    assert concentration(1, "%", None, None) == concentration(10, "mg", 1, "g")
    assert quantity(5, "furlongs") is None


# --------------------------------------------------------------------------- CSV columns


def test_csv_drug_columns():
    row = {
        "ingredient": "amoxicillin; clavulanate",
        "amount_value": "875;125",
        "amount_unit": "mg",  # one unit for both ingredients
        "dose_form": "Oral Tablet",
    }
    structure = parse_drug_structure(row)
    assert [c["amount_unit"] for c in structure["components"]] == ["mg", "mg"]
    assert structure["components"][1] == {
        "ingredient": "clavulanate",
        "amount_value": 125.0,
        "amount_unit": "mg",
    }
    assert (
        parse_drug_structure({"ingredient": "x", "numerator_value": "0,5"})["components"][0][
            "numerator_value"
        ]
        == 0.5
    )
    assert parse_drug_structure({"source_value": "A"}) is None
    with pytest.raises(SourceImportError, match="2 values for 3"):
        parse_drug_structure({"ingredient": "a;b;c", "amount_value": "1;2"})
    with pytest.raises(SourceImportError, match="ingredient"):
        parse_drug_structure({"dose_form": "Oral Tablet"})


# --------------------------------------------------------------------------- matcher


def test_clinical_drug_with_unit_conversion(drug_vocabulary):
    match = _match(
        _structure(
            {"ingredient": "Acetaminophen", "amount_value": 0.5, "amount_unit": "g"},
            form="oral tablet",
        )
    )
    # RxNorm wins over the RxNorm Extension duplicate (9032)
    assert (match.level, match.concept_ids) == ("clinical_drug", [3])


def test_combination_drug_needs_every_ingredient(drug_vocabulary):
    both = _structure(
        {"ingredient": "amoxicillin", "amount_value": 875, "amount_unit": "mg"},
        {"ingredient": "clavulanate", "amount_value": 125, "amount_unit": "mg"},
        form="Oral Tablet",
    )
    assert _match(both).concept_ids == [9031]
    only_amoxicillin = _structure(
        {"ingredient": "amoxicillin", "amount_value": 875, "amount_unit": "mg"}, form="Oral Tablet"
    )
    assert _match(only_amoxicillin).concept_ids == []  # not the combination product


def test_concentration_and_rxnorm_extension(drug_vocabulary):
    liquid = _structure(
        {
            "ingredient": "acetaminophen",
            "numerator_value": 500,
            "numerator_unit": "mg",
            "denominator_value": 5,
            "denominator_unit": "ml",
        },
        form="Oral Solution",
    )
    assert _match(liquid).concept_ids == [9030]
    european = _structure(
        {"ingredient": "amoxicillin", "amount_value": 500, "amount_unit": "mg"}, form="Oral Tablet"
    )
    assert _match(european).concept_ids == [9035]  # exists only in RxNorm Extension


def test_less_detail_maps_to_less_specific_levels(drug_vocabulary):
    assert _match(_structure({"ingredient": "acetaminophen"}, form="Oral Tablet")).concept_ids == [
        9033
    ]
    comp = _match(
        _structure({"ingredient": "acetaminophen", "amount_value": 500, "amount_unit": "mg"})
    )
    assert (comp.level, comp.concept_ids) == ("clinical_drug_comp", [9034])
    # Ingredient only: the one Clinical Drug Form that exists, as requested
    only = _match(_structure({"ingredient": "acetaminophen"}))
    assert (only.level, only.concept_ids) == ("clinical_drug_form", [9033])


def test_wrong_strength_or_unknown_names_do_not_match(drug_vocabulary):
    assert (
        _match(
            _structure(
                {"ingredient": "acetaminophen", "amount_value": 650, "amount_unit": "mg"},
                form="Oral Tablet",
            )
        ).concept_ids
        == []
    )
    unknown = _match(
        _structure(
            {"ingredient": "paracetamolum", "amount_value": 500, "amount_unit": "mg"}, form="pilula"
        )
    )
    assert unknown.unresolved == ["paracetamolum", "pilula"]


# --------------------------------------------------------------------------- pipeline


async def test_parsed_name_is_matched_deterministically(drug_vocabulary):
    from app.workflows.service import run_pipeline

    parser = FakeParser(
        ParsedDrug(
            is_drug=True,
            components=[
                DrugComponent(ingredient="acetaminophen", amount_value=500, amount_unit="mg")
            ],
            dose_form="Oral Tablet",
        )
    )
    result = await run_pipeline(
        drug_state("Paracetamol 500 mg tbl"), drug_ctx(parser), "automap_drug"
    )
    assert result["outcome"] == "accepted" and result["selected"]["concept_id"] == 3
    assert result["confidence"] == 10 and result["method"] == "auto_drug_structured"
    assert [t["node"] for t in result["trace"]] == [
        "extract_atc7",
        "match_previous",
        "match_code",
        "match_drug",
        "gate",
    ]


async def test_provided_structure_skips_the_parser_and_reads_local_names(drug_vocabulary):
    from app.workflows.service import run_pipeline

    parser = FakeParser(ParsedDrug(is_drug=False))
    structure = parse_drug_structure(
        {
            "ingredient": "paracetamolum",
            "amount_value": "500",
            "amount_unit": "mg",
            "dose_form": "tableta",
        }
    )
    result = await run_pipeline(
        drug_state("Paracetamol 500 mg tbl", structure), drug_ctx(parser), "automap_drug"
    )
    assert result["selected"]["concept_id"] == 3 and parser.calls == []
    assert "provided" in result["rationale"]


async def test_no_deterministic_match_falls_back_with_context(drug_vocabulary):
    from app.llm.structured import RerankResult
    from app.workflows.service import run_pipeline

    class Recording:
        notes = None

        async def rerank(self, term, candidates, drug_specific, **kwargs):
            Recording.notes = kwargs.get("notes")
            return RerankResult(None, None)

    parser = FakeParser(
        ParsedDrug(
            is_drug=True,
            components=[
                DrugComponent(ingredient="acetaminophen", amount_value=650, amount_unit="mg")
            ],
            dose_form="Oral Tablet",
        )
    )
    result = await run_pipeline(
        drug_state("Paracetamol 650 mg tbl"), drug_ctx(parser, Recording()), "automap_drug"
    )
    assert [t["node"] for t in result["trace"]][:8] == [
        "extract_atc7",
        "match_previous",
        "match_code",
        "match_drug",
        "match_name",
        "translate",
        "match_english",
        "retrieve",
    ]
    assert Recording.notes == ["Parsed drug structure (parsed): acetaminophen 650 mg; Oral Tablet"]


def test_csv_structure_drives_automap(
    client, user_headers, embedded, fake_reranker, drug_vocabulary, admin_headers
):
    from tests.conftest import import_source_csv

    csv = (
        b"source_value,source_concept_name,ingredient,amount_value,amount_unit,dose_form\n"
        b"J01CR02-1,Amoksiklav 875/125 film tbl,amoxicillin;clavulanate,875;125,mg,Oral Tablet\n"
    )
    task = import_source_csv(client, admin_headers, 50, csv, is_drug="true")
    assert task["result"]["with_drug_structure"] == 1
    resp = client.post(
        "/api/source-vocabularies/50/automap",
        json={"domains": ["Drug"], "drug_specific": True},
        headers=admin_headers,
    )
    task = client.get(f"/api/tasks/{resp.json()['task_id']}", headers=admin_headers).json()
    assert task["params"]["pipeline"] == "automap_drug" and task["result"]["queued"] == 1
    suggestions = client.get("/api/source-vocabularies/50/suggestions", headers=user_headers).json()
    item = suggestions["items"][0]
    assert item["concept_id"] == 9031 and item["confidence"] == 10


# --------------------------------------------------------------------------- brands


def _branded(brand, *components, form=None):
    return DrugStructure(
        components=[DrugComponent(**c) for c in components], dose_form=form, brand_name=brand
    )


PARACETAMOL_500 = {"ingredient": "acetaminophen", "amount_value": 500, "amount_unit": "mg"}


def test_brand_with_full_structure_maps_to_branded_drug(drug_vocabulary):
    match = _match(
        _branded(
            "augmentin",
            {"ingredient": "amoxicillin", "amount_value": 875, "amount_unit": "mg"},
            {"ingredient": "clavulanate", "amount_value": 125, "amount_unit": "mg"},
            form="Oral Tablet",
        )
    )
    assert (match.level, match.concept_ids) == ("branded_drug", [9041])


def test_brand_alone_takes_ingredients_from_its_products(drug_vocabulary):
    # Brand + form, no ingredients or strength: no Branded Drug Form exists -> Clinical Drug Form
    form_only = _match(_branded("Lekadol", form="Oral Tablet"))
    assert (form_only.level, form_only.concept_ids) == ("clinical_drug_form", [9033])
    full = _match(_branded("Lekadol", PARACETAMOL_500, form="Oral Tablet"))
    assert (full.level, full.concept_ids) == ("branded_drug", [9043])  # RxNorm Extension brand


def test_brand_without_branded_product_falls_to_clinical(drug_vocabulary):
    match = _match(_branded("Augmentin", PARACETAMOL_500, form="Oral Tablet"))
    assert (match.level, match.concept_ids) == ("clinical_drug", [3])
    assert "clinical level" in match.reason
    assert _match(_branded("Nonexistent", PARACETAMOL_500, form="Oral Tablet")).concept_ids == [3]


def test_ingredients_only_without_a_form_map_to_every_ingredient(drug_vocabulary):
    # amoxicillin + clavulanate has exactly one Clinical Drug Form
    pair = _structure({"ingredient": "amoxicillin"}, {"ingredient": "clavulanate"})
    assert _match(pair).concept_ids == [9044]
    # no Clinical Drug Form for this pair: one mapping per ingredient
    match = _match(_structure({"ingredient": "acetaminophen"}, {"ingredient": "amoxicillin"}))
    assert (match.level, match.concept_ids, match.combined) == ("ingredient", [9001, 9002], True)


def test_combined_match_goes_through_review(
    client, user_headers, embedded, fake_reranker, drug_vocabulary, admin_headers
):
    from tests.conftest import import_source_csv

    csv = (
        b"source_value,source_concept_name,ingredient\n"
        b"X-COMBI,Paracetamol + amoksicilin,acetaminophen;amoxicillin\n"
    )
    import_source_csv(client, admin_headers, 51, csv, is_drug="true")
    resp = client.post(
        "/api/source-vocabularies/51/automap",
        json={"domains": ["Drug"], "drug_specific": True},
        headers=admin_headers,
    )
    task = client.get(f"/api/tasks/{resp.json()['task_id']}", headers=admin_headers).json()
    assert task["result"]["queued"] == 1
    items = client.get("/api/source-vocabularies/51/suggestions", headers=user_headers).json()
    item = items["items"][0]
    assert item["concept_id"] == 9001
    assert [c["concept_id"] for c in item["extra_concepts"]] == [9002]
    client.post("/api/suggestions/accept", json={"ids": [item["id"]]}, headers=user_headers)
    mapped = client.get("/api/source-vocabularies/51/mapped", headers=admin_headers).json()
    assert sorted(r["concept_id"] for r in mapped["items"]) == [9001, 9002]


# --------------------------------------------------------------------------- name resolution


def test_names_resolve_through_atc_salts_and_synonyms(drug_vocabulary):
    tablet = {"amount_value": 500, "amount_unit": "mg"}
    # ATC 5th name (INN) of a single-ingredient code
    atc = _match(_structure({"ingredient": "Paracetamol", **tablet}, form="Oral Tablet"))
    assert atc.concept_ids == [3] and atc.approximate == []
    assert atc.resolved == ["Paracetamol → acetaminophen (ATC name)"]
    # Precise Ingredient (salt) -> Maps to the ingredient
    salt = _match(
        _structure({"ingredient": "amoxicillin trihydrate", **tablet}, form="Oral Tablet")
    )
    assert salt.concept_ids == [9035] and "Maps to" in salt.resolved[0]
    # RxNorm synonym, accents and case ignored
    assert _match(_structure({"ingredient": "apap", **tablet}, form="Oral Tablet")).concept_ids == [
        3
    ]
    # A combination ATC code maps to two ingredients: its name resolves nothing
    combi = _match(_structure({"ingredient": "amoxicillin and enzyme inhibitor"}))
    assert combi.unresolved == ["amoxicillin and enzyme inhibitor"]


def test_fuzzy_names_only_on_request_and_marked_approximate(drug_vocabulary):
    typo = _structure(
        {"ingredient": "amoxicilin", "amount_value": 500, "amount_unit": "mg"}, form="Oral Tablet"
    )
    assert _match(typo).unresolved == ["amoxicilin"]
    with Session(get_engine()) as session:
        fuzzy = drug_matching.match_structure(session, typo, fuzzy=True)
        far = drug_matching.match_structure(
            session, _structure({"ingredient": "zolpidem"}), fuzzy=True
        )
    assert fuzzy.concept_ids == [9035]
    assert fuzzy.approximate and "similarity" in fuzzy.approximate[0]
    assert far.unresolved == ["zolpidem"]


def test_related_dose_form_is_approximate(drug_vocabulary):
    # No amoxicillin 250 mg Oral Tablet, but a Chewable Tablet in the same group (Pill)
    match = _match(
        _structure(
            {"ingredient": "amoxicillin", "amount_value": 250, "amount_unit": "mg"},
            form="Oral Tablet",
        )
    )
    assert match.concept_ids == [9036]
    assert match.approximate == ["no drug in the form 'Oral Tablet'; related forms (Pill)"]


# --------------------------------------------------------------------------- quantity


def _diazepam(*, quantity=None, form="Injectable Solution", **strength):
    return DrugStructure(
        components=[DrugComponent(ingredient="diazepam", **strength)],
        dose_form=form,
        quantity_value=quantity,
        quantity_unit="mL" if quantity else None,
    )


def test_quantity_selects_the_quant_drug(drug_vocabulary):
    per_2ml = {"numerator_value": 10, "numerator_unit": "mg", "denominator_value": 2}
    per_2ml["denominator_unit"] = "mL"
    quant = _match(_diazepam(quantity=2, **per_2ml))
    assert (quant.level, quant.concept_ids, quant.approximate) == (
        "quant_clinical_drug",
        [9037],
        [],
    )
    # Concentration only: the Clinical Drug
    assert _match(_diazepam(**per_2ml)).concept_ids == [9038]
    # Another volume: no 5 mL Quant drug, the Clinical Drug keeps the concentration
    other = _match(_diazepam(quantity=5, **per_2ml))
    assert other.concept_ids == [9038] and "used the one without" in other.reason
    # Amount + quantity is the total amount in the total volume, as OMOP stores Quant drugs
    total = _match(
        _diazepam(quantity=2, amount_value=10, amount_unit="mg", form="Injectable Solution")
    )
    assert (total.concept_ids, total.approximate) == ([9037], [])


def test_amount_without_quantity_is_approximate(drug_vocabulary):
    match = _match(_diazepam(amount_value=10, amount_unit="mg"))
    assert match.concept_ids == [9037] and match.approximate


def test_csv_quantity_columns():
    structure = parse_drug_structure(
        {"ingredient": "diazepam", "amount_value": "10", "amount_unit": "mg"}
        | {"quantity_value": "2", "quantity_unit": "mL"}
    )
    assert (structure["quantity_value"], structure["quantity_unit"]) == (2.0, "mL")
    with pytest.raises(SourceImportError, match="quantity_unit"):
        parse_drug_structure({"ingredient": "diazepam", "quantity_value": "2"})


# --------------------------------------------------------------------------- pipeline


async def test_approximate_match_goes_to_the_reranker_first(drug_vocabulary):
    from app.llm.structured import RerankResult
    from app.workflows.service import run_pipeline

    class Recording:
        candidates = None
        notes = None

        async def rerank(self, term, candidates, drug_specific, **kwargs):
            Recording.candidates = Recording.candidates or [c["concept_id"] for c in candidates]
            Recording.notes = kwargs.get("notes")
            return RerankResult(None, None)

    parser = FakeParser(
        ParsedDrug(
            is_drug=True,
            components=[DrugComponent(ingredient="amoxicilin", amount_value=500, amount_unit="mg")],
            dose_form="Oral Tablet",
        )
    )
    result = await run_pipeline(
        drug_state("Amoksicilin 500 mg tbl"), drug_ctx(parser, Recording()), "automap_drug"
    )
    drug_step = next(t for t in result["trace"] if t["node"] == "match_drug")
    assert "approximate" in drug_step["summary"]
    assert Recording.candidates[0] == 9035
    assert any(n.startswith("Structured match is approximate") for n in Recording.notes)
