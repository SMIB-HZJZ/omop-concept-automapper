"""Deterministic drug mapping on drug_strength: units, CSV structure, matcher and pipeline."""

import pytest
from sqlalchemy import text
from sqlmodel import Session

from app.core import cache
from app.core.db import get_engine
from app.llm.schemas import DrugComponent, DrugStructure, ParsedDrug
from app.services import drug_matching
from app.services.drug_units import close, concentration, quantity
from app.services.source_import import SourceImportError, parse_drug_structure

CONCEPTS = [
    # id, name, domain, vocabulary, class, standard, code
    (9001, "acetaminophen", "Drug", "RxNorm", "Ingredient", "S", "161"),
    (9002, "amoxicillin", "Drug", "RxNorm", "Ingredient", "S", "723"),
    (9003, "clavulanate", "Drug", "RxNorm", "Ingredient", "S", "48203"),
    (9010, "Oral Tablet", "Drug", "RxNorm", "Dose Form", None, "317541"),
    (9011, "Oral Solution", "Drug", "RxNorm", "Dose Form", None, "316968"),
    (9020, "milligram", "Unit", "UCUM", "Unit", "S", "mg"),
    (9021, "milliliter", "Unit", "UCUM", "Unit", "S", "mL"),
    (9030, "acetaminophen 100 MG/ML Oral Solution", "Drug", "RxNorm", "Clinical Drug", "S", "9030"),
    (
        9031,
        "amoxicillin 875 MG / clavulanate 125 MG Oral Tablet",
        "Drug",
        "RxNorm",
        "Clinical Drug",
        "S",
        "9031",
    ),
    (
        9032,
        "acetaminophen 500 MG Oral Tablet",
        "Drug",
        "RxNorm Extension",
        "Clinical Drug",
        "S",
        "OMOP9032",
    ),
    (9033, "acetaminophen Oral Tablet", "Drug", "RxNorm", "Clinical Drug Form", "S", "9033"),
    (9034, "acetaminophen 500 MG", "Drug", "RxNorm", "Clinical Drug Comp", "S", "9034"),
    (
        9035,
        "amoxicillin 500 MG Oral Tablet",
        "Drug",
        "RxNorm Extension",
        "Clinical Drug",
        "S",
        "OMOP9035",
    ),
    (9040, "Augmentin", "Drug", "RxNorm", "Brand Name", None, "151392"),
    (
        9041,
        "amoxicillin 875 MG / clavulanate 125 MG Oral Tablet [Augmentin]",
        "Drug",
        "RxNorm",
        "Branded Drug",
        "S",
        "9041",
    ),
    (9042, "Lekadol", "Drug", "RxNorm Extension", "Brand Name", None, "OMOP9042"),
    (
        9043,
        "acetaminophen 500 MG Oral Tablet [Lekadol]",
        "Drug",
        "RxNorm Extension",
        "Branded Drug",
        "S",
        "OMOP9043",
    ),
    (
        9044,
        "amoxicillin / clavulanate Oral Tablet",
        "Drug",
        "RxNorm",
        "Clinical Drug Form",
        "S",
        "9044",
    ),
    # Name resolution beyond the exact Ingredient name
    (9100, "paracetamol; oral, parenteral, rectal", "Drug", "ATC", "ATC 5th", "C", "N02BE01"),
    (9101, "amoxicillin and enzyme inhibitor", "Drug", "ATC", "ATC 5th", "C", "J01CR02"),
    (9102, "amoxicillin trihydrate", "Drug", "RxNorm", "Precise Ingredient", None, "9102"),
    # Dose form group and a drug only in a related form
    (9012, "Pill", "Drug", "RxNorm", "Dose Form Group", "C", "9012"),
    (9013, "Chewable Tablet", "Drug", "RxNorm", "Dose Form", None, "317544"),
    (9036, "amoxicillin 250 MG Chewable Tablet", "Drug", "RxNorm", "Clinical Drug", "S", "9036"),
    # Quantified injectables
    (9004, "diazepam", "Drug", "RxNorm", "Ingredient", "S", "3322"),
    (9014, "Injectable Solution", "Drug", "RxNorm", "Dose Form", None, "316949"),
    (
        9037,
        "2 ML diazepam 5 MG/ML Injectable Solution",
        "Drug",
        "RxNorm",
        "Quant Clinical Drug",
        "S",
        "9037",
    ),
    (9038, "diazepam 5 MG/ML Injectable Solution", "Drug", "RxNorm", "Clinical Drug", "S", "9038"),
]
HAS_FORM = [
    (3, 9010),
    (9030, 9011),
    (9031, 9010),
    (9032, 9010),
    (9033, 9010),
    (9035, 9010),
    (9041, 9010),
    (9043, 9010),
    (9044, 9010),
    (9036, 9013),
    (9037, 9014),
    (9038, 9014),
]
HAS_ING = [(9033, 9001), (9044, 9002), (9044, 9003)]
MAPS_TO = [(9100, 9001), (9101, 9002), (9101, 9003), (9102, 9002)]
IS_A = [(9010, 9012), (9013, 9012)]
SYNONYMS = [(9001, "APAP")]
HAS_BRAND = [(9041, 9040), (9043, 9042)]
STRENGTHS = [
    # drug, ingredient, amount, amount unit, numerator, numerator unit, denominator unit
    (3, 9001, 500, 9020, None, None, None),
    (9030, 9001, None, None, 100, 9020, 9021),
    (9031, 9002, 875, 9020, None, None, None),
    (9031, 9003, 125, 9020, None, None, None),
    (9032, 9001, 500, 9020, None, None, None),
    (9034, 9001, 500, 9020, None, None, None),
    (9035, 9002, 500, 9020, None, None, None),
    (9041, 9002, 875, 9020, None, None, None),
    (9041, 9003, 125, 9020, None, None, None),
    (9043, 9001, 500, 9020, None, None, None),
    (9036, 9002, 250, 9020, None, None, None),
    (9038, 9004, None, None, 5, 9020, 9021),
]
# Quant drugs: numerator is the total amount in denominator_value
QUANT_STRENGTHS = [(9037, 9004, 10, 9020, 2, 9021)]
IDS = [c[0] for c in CONCEPTS]


@pytest.fixture(scope="module")
def drug_vocabulary(loaded_vocabulary):
    with get_engine().begin() as conn:
        for c in CONCEPTS:
            conn.execute(
                text(
                    "INSERT INTO concept VALUES (:id, :name, :domain, :vocab, :cls, :std, :code, "
                    "'1970-01-01', '2099-12-31', NULL)"
                ),
                dict(zip(["id", "name", "domain", "vocab", "cls", "std", "code"], c, strict=True)),
            )
        for rel, pairs in (
            ("RxNorm has dose form", HAS_FORM),
            ("RxNorm has ing", HAS_ING),
            ("Has brand name", HAS_BRAND),
            ("Maps to", MAPS_TO),
            ("RxNorm is a", IS_A),
        ):
            for a, b in pairs:
                conn.execute(
                    text(
                        "INSERT INTO concept_relationship VALUES (:a, :b, :r, '1970-01-01', "
                        "'2099-12-31', NULL)"
                    ),
                    {"a": a, "b": b, "r": rel},
                )
        for s in STRENGTHS:
            conn.execute(
                text(
                    "INSERT INTO drug_strength (drug_concept_id, ingredient_concept_id, amount_value, "
                    "amount_unit_concept_id, numerator_value, numerator_unit_concept_id, "
                    "denominator_unit_concept_id, valid_start_date, valid_end_date) "
                    "VALUES (:d, :i, :a, :au, :n, :nu, :du, '1970-01-01', '2099-12-31')"
                ),
                dict(zip(["d", "i", "a", "au", "n", "nu", "du"], s, strict=True)),
            )
        for s in QUANT_STRENGTHS:
            conn.execute(
                text(
                    "INSERT INTO drug_strength (drug_concept_id, ingredient_concept_id, "
                    "numerator_value, numerator_unit_concept_id, denominator_value, "
                    "denominator_unit_concept_id, valid_start_date, valid_end_date) "
                    "VALUES (:d, :i, :n, :nu, :dv, :du, '1970-01-01', '2099-12-31')"
                ),
                dict(zip(["d", "i", "n", "nu", "dv", "du"], s, strict=True)),
            )
        for cid, name in SYNONYMS:
            conn.execute(
                text("INSERT INTO concept_synonym VALUES (:c, :n, 4180186)"), {"c": cid, "n": name}
            )
    cache.invalidate("vocab")
    yield
    with get_engine().begin() as conn:
        conn.execute(text("DELETE FROM drug_strength"))
        conn.execute(text("DELETE FROM concept_synonym WHERE concept_id = ANY(:ids)"), {"ids": IDS})
        conn.execute(
            text(
                "DELETE FROM concept_relationship WHERE concept_id_1 = ANY(:ids) OR concept_id_1 = 3"
            ),
            {"ids": IDS},
        )
        conn.execute(
            text("DELETE FROM source_standard_map WHERE concept_id = ANY(:ids)"), {"ids": IDS}
        )
        conn.execute(
            text("DELETE FROM mapping_suggestion WHERE concept_id = ANY(:ids)"), {"ids": IDS}
        )
        conn.execute(text("DELETE FROM concept WHERE concept_id = ANY(:ids)"), {"ids": IDS})
    cache.invalidate("vocab")


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


class FakeParser:
    def __init__(self, parsed: ParsedDrug):
        self.parsed = parsed
        self.calls = []

    async def parse(self, term, hint=None):
        self.calls.append((term, hint))
        return self.parsed


class FakeTranslator:
    async def translate(self, term):
        from app.llm.structured import TranslationResult

        return TranslationResult(
            {"paracetamolum": "acetaminophen", "tableta": "Oral Tablet"}.get(term, term), "hr"
        )


def _ctx(parser=None, reranker=None):
    import dataclasses

    from app.workflows.service import _get_concepts, _match_drug
    from tests.workflows.helpers import FakeSearcher, make_context

    ctx = make_context(
        reranker,
        searcher=FakeSearcher(),
        drug_parser=parser,
        drug_matcher=_match_drug,
        translator=FakeTranslator(),
    )
    return dataclasses.replace(ctx, get_concepts=_get_concepts)


def _state(name, structure=None):
    from app.workflows.service import initial_state

    return initial_state(
        source_id=1,
        source_value="N02BE01-1",
        source_name=name,
        domains=["Drug"],
        drug_specific=True,
        threshold=8,
        drug_structure=structure,
    )


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
    result = await run_pipeline(_state("Paracetamol 500 mg tbl"), _ctx(parser), "automap_drug")
    assert result["outcome"] == "accepted" and result["selected"]["concept_id"] == 3
    assert result["confidence"] == 10 and result["method"] == "auto_drug_structured"
    assert [t["node"] for t in result["trace"]] == [
        "extract_atc7",
        "match_previous",
        "match_code",
        "match_drug",
        "gate",
    ]


async def test_provided_structure_skips_the_parser_and_translates_local_names(drug_vocabulary):
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
        _state("Paracetamol 500 mg tbl", structure), _ctx(parser), "automap_drug"
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
        _state("Paracetamol 650 mg tbl"), _ctx(parser, Recording()), "automap_drug"
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


def test_dose_form_aliases():
    from app.services.drug_forms import alias_targets

    assert alias_targets("Filmom obložena tableta") == ["Oral Tablet"]
    assert alias_targets("Film-coated tablet") == ["Oral Tablet"]
    assert alias_targets("otopina za injekciju") == ["Injectable Solution", "Injection"]
    assert alias_targets("Oral Tablet") == []


def test_related_dose_form_is_approximate(drug_vocabulary):
    # No amoxicillin 250 mg Oral Tablet, but a Chewable Tablet in the same group (Pill)
    match = _match(
        _structure(
            {"ingredient": "amoxicillin", "amount_value": 250, "amount_unit": "mg"},
            form="film tbl",
        )
    )
    assert match.concept_ids == [9036]
    assert match.approximate == ["no drug in the form 'film tbl'; related forms (Pill)"]


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
        _diazepam(quantity=2, amount_value=10, amount_unit="mg", form="otopina za injekciju")
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
        _state("Amoksicilin 500 mg tbl"), _ctx(parser, Recording()), "automap_drug"
    )
    drug_step = next(t for t in result["trace"] if t["node"] == "match_drug")
    assert "approximate" in drug_step["summary"]
    assert Recording.candidates[0] == 9035
    assert any(n.startswith("Structured match is approximate") for n in Recording.notes)
