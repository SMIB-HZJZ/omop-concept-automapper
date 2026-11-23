"""Shared drug test data: a small RxNorm-like vocabulary with drug_strength rows, and fakes for
the LLM steps of drug mapping (parser, translator, dose form picker)."""

import dataclasses

import pytest
from sqlalchemy import text

from app.core import cache
from app.core.db import get_engine
from app.llm.schemas import ParsedDrug

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


class FakeParser:
    """Returns a fixed ParsedDrug, or one per source name from a dict."""

    def __init__(self, parsed: ParsedDrug | dict[str, ParsedDrug]):
        self.parsed = parsed
        self.calls = []

    async def parse(self, term, hint=None):
        self.calls.append((term, hint))
        if isinstance(self.parsed, dict):
            return self.parsed.get(term, ParsedDrug(is_drug=False))
        return self.parsed


class FakeTranslator:
    def __init__(self, english: dict[str, str] | None = None):
        self.english = english or {"paracetamolum": "acetaminophen"}

    async def translate(self, term, context=None, language=None):
        from app.llm.structured import TranslationResult

        return TranslationResult(self.english.get(term, term), None)


class FakeFormPicker:
    def __init__(self, forms: dict[str, list[str]] | None = None):
        self.forms = forms or {"tableta": ["Oral Tablet"]}
        self.calls = []

    async def pick(self, form, product=None):
        self.calls.append((form, product))
        return self.forms.get(form, [])


def drug_ctx(parser=None, reranker=None, translator=None, picker=None):
    from app.workflows.service import _get_concepts, _match_drug
    from tests.workflows.helpers import FakeSearcher, make_context

    ctx = make_context(
        reranker,
        searcher=FakeSearcher(),
        drug_parser=parser,
        drug_matcher=_match_drug,
        translator=translator or FakeTranslator(),
    )
    return dataclasses.replace(
        ctx, get_concepts=_get_concepts, dose_form_picker=picker or FakeFormPicker()
    )


def drug_state(name, structure=None, source_value="N02BE01-1"):
    from app.workflows.service import initial_state

    return initial_state(
        source_id=1,
        source_value=source_value,
        source_name=name,
        domains=["Drug"],
        drug_specific=True,
        threshold=8,
        drug_structure=structure,
    )
