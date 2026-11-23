"""Synthetic drug lists in several languages, run through the whole automap_drug pipeline.

The LLM steps are fakes with fixed answers (what a good model would say), so these tests check the
pipeline logic: which step decides, what is accepted at confidence 10 and what is left to the
reranker. The quality of the real LLM steps is measured by ``scripts/eval_drug_pipeline.py``.
"""

from dataclasses import dataclass, field

import pytest

from app.llm.schemas import DrugComponent, ParsedDrug
from app.services.source_import import parse_drug_structure
from tests.workflows.drug_fixtures import (
    FakeFormPicker,
    FakeParser,
    FakeTranslator,
    drug_ctx,
    drug_state,
)

# What the fake LLM steps answer: dose form terms in any language, ingredient translations and
# parsed names
FORMS = {
    "tableta": ["Oral Tablet"],
    "Filmtablette": ["Oral Tablet"],
    "compressa rivestita con film": ["Oral Tablet"],
    "comprimé pelliculé": ["Oral Tablet"],
    "tabletka powlekana": ["Oral Tablet"],
    "otopina za injekciju": ["Injectable Solution", "Injection"],
    "Injektionslösung": ["Injectable Solution", "Injection"],
    "soluzione orale": ["Oral Solution"],
    "Kautablette": ["Chewable Tablet"],
    "Tablet": ["Oral Tablet"],
}
ENGLISH = {
    "paracetamolo": "acetaminophen",
    "Paracetamol": "acetaminophen",
    "amoxicilline": "amoxicillin",
    "Diazepamum": "diazepam",
    "paracetamolum": "acetaminophen",
}


def _parsed(*components, form=None, brand=None, quantity=None) -> ParsedDrug:
    return ParsedDrug(
        is_drug=True,
        components=[DrugComponent(**c) for c in components],
        dose_form=form,
        brand_name=brand,
        quantity_value=quantity,
        quantity_unit="mL" if quantity else None,
    )


PARSED = {
    "Augmentin 875 mg/125 mg film-coated tablets": _parsed(
        {"ingredient": "amoxicillin", "amount_value": 875, "amount_unit": "mg"},
        {"ingredient": "clavulanate", "amount_value": 125, "amount_unit": "mg"},
        form="Oral Tablet",
        brand="Augmentin",
    ),
    "Lekadol 500 mg tablete": _parsed(
        {"ingredient": "acetaminophen", "amount_value": 500, "amount_unit": "mg"},
        form="Oral Tablet",
        brand="Lekadol",
    ),
    "Paracetamol 500 mg/5 ml Sirup": _parsed(
        {
            "ingredient": "acetaminophen",
            "numerator_value": 500,
            "numerator_unit": "mg",
            "denominator_value": 5,
            "denominator_unit": "mL",
        },
        form="Oral Solution",
    ),
    # The parser keeps a local form name the vocabulary does not know: the picker decides
    "Amoxicillin 250 mg Kautabletten": _parsed(
        {"ingredient": "amoxicillin", "amount_value": 250, "amount_unit": "mg"},
        form="Kautablette",
    ),
    "Diazepam 10 mg/2 ml ampule": _parsed(
        {
            "ingredient": "diazepam",
            "numerator_value": 10,
            "numerator_unit": "mg",
            "denominator_value": 2,
            "denominator_unit": "mL",
        },
        form="Injectable Solution",
        quantity=2,
    ),
    # The parser rewrites a unit the matcher cannot read
    "Paracetamol 500 Milligramm Tabletten": _parsed(
        {"ingredient": "acetaminophen", "amount_value": 500, "amount_unit": "mg"},
        form="Oral Tablet",
    ),
    "Normabel 10 mg/2 ml Injektionslösung": _parsed(
        {
            "ingredient": "diazepam",
            "numerator_value": 10,
            "numerator_unit": "mg",
            "denominator_value": 2,
            "denominator_unit": "mL",
        },
        form="Injectable Solution",
        brand="Normabel",
        quantity=2,
    ),
    "Paracetamol 650 mg tbl": _parsed(
        {"ingredient": "acetaminophen", "amount_value": 650, "amount_unit": "mg"},
        form="Oral Tablet",
    ),
}


@dataclass
class Case:
    name: str
    columns: dict | None = None  # CSV drug columns; None = parse the name
    accepted: list[int] | None = None  # concepts accepted by match_drug (confidence 10)
    first_candidate: int | None = None  # else: the reranker's first candidate
    approximate: bool = False
    note: str = ""  # a text the match_drug summary or the notes must contain
    picker_used: bool | None = None
    parser_used: bool | None = None
    extra: dict = field(default_factory=dict)


def _cols(ingredient, form=None, **values) -> dict:
    row = {"ingredient": ingredient, "dose_form": form or ""}
    return row | {k: str(v) for k, v in values.items()}


CASES = [
    # --- provided structure, ingredient and form in the source language
    Case(
        "hr: Paracetamol 500 mg tableta",
        _cols("paracetamol", "tableta", amount_value=500, amount_unit="mg"),
        accepted=[3],
        note="ATC name",
        picker_used=True,
        parser_used=False,
    ),
    Case(
        "de: Paracetamol 500 mg Filmtablette",
        _cols("Paracetamol", "Filmtablette", amount_value=500, amount_unit="mg"),
        accepted=[3],
        picker_used=True,
    ),
    Case(
        "it: paracetamolo 0,5 g compressa rivestita con film",
        _cols("paracetamolo", "compressa rivestita con film", amount_value="0,5", amount_unit="g"),
        accepted=[3],
    ),
    Case(
        "fr: amoxicilline 500 mg comprimé pelliculé",
        _cols("amoxicilline", "comprimé pelliculé", amount_value=500, amount_unit="mg"),
        accepted=[9035],
    ),
    Case(
        "pl: amoxicillin trihydrate 500 mg tabletka powlekana",
        _cols("amoxicillin trihydrate", "tabletka powlekana", amount_value=500, amount_unit="mg"),
        accepted=[9035],
        note="Maps to",
    ),
    Case(
        "en: APAP 500 mg Oral Tablet (synonym, RxNorm form)",
        _cols("APAP", "Oral Tablet", amount_value=500, amount_unit="mg"),
        accepted=[3],
        picker_used=False,
    ),
    Case(
        "de: Diazepamum 10 mg/2 ml Injektionslösung, Ampulle 2 ml",
        _cols(
            "Diazepamum",
            "Injektionslösung",
            numerator_value=10,
            numerator_unit="mg",
            denominator_value=2,
            denominator_unit="ml",
            quantity_value=2,
            quantity_unit="ml",
        ),
        accepted=[9037],
    ),
    Case(
        "hr: diazepam 5 mg/ml otopina za injekciju (no quantity)",
        _cols(
            "diazepam",
            "otopina za injekciju",
            numerator_value=5,
            numerator_unit="mg",
            denominator_unit="mL",
        ),
        accepted=[9038],
    ),
    Case(
        "hr: Paracetamol + amoksicilin (ingredients only)",
        _cols("paracetamolum;amoxicillin"),
        accepted=[9001, 9002],
    ),
    Case(
        "en: acetaminophen 500 mg (no form)",
        _cols("acetaminophen", amount_value=500, amount_unit="mg"),
        accepted=[9034],
    ),
    # --- approximate: to the reranker as its first candidate
    Case(
        "hr: amoksicilin (typo) 500 mg tableta",
        _cols("amoxicilin", "tableta", amount_value=500, amount_unit="mg"),
        first_candidate=9035,
        approximate=True,
        note="similarity",
    ),
    Case(
        "de: Amoxicillin 250 mg Filmtablette (exists only as chewable)",
        _cols("amoxicillin", "Filmtablette", amount_value=250, amount_unit="mg"),
        first_candidate=9036,
        approximate=True,
        note="related forms (Pill)",
    ),
    Case(
        "de: Diazepam 10 mg Injektionslösung (amount, volume unknown)",
        _cols("diazepam", "Injektionslösung", amount_value=10, amount_unit="mg"),
        first_candidate=9037,
        approximate=True,
    ),
    Case(
        "en: acetaminophen 500 mg Tablet (generic RxNorm form)",
        _cols("acetaminophen", "Tablet", amount_value=500, amount_unit="mg"),
        accepted=[3],
        picker_used=True,
        note="chosen by the LLM",
    ),
    Case(
        "de: Paracetamol 500 Milligramm Tabletten (unit unknown to the matcher)",
        _cols("acetaminophen", "Oral Tablet", amount_value=500, amount_unit="Milligramm"),
        accepted=[3],
        parser_used=True,
    ),
    Case(
        "de: Normabel 10 mg/2 ml Injektionslösung (brand without the quantity)",
        first_candidate=9037,
        note="branded drug without the quantity or clinical drug with it",
    ),
    # --- names parsed by the LLM
    Case("en: Augmentin 875 mg/125 mg film-coated tablets", accepted=[9041], parser_used=True),
    Case("hr: Lekadol 500 mg tablete", accepted=[9043]),
    Case("de: Paracetamol 500 mg/5 ml Sirup", accepted=[9030]),
    Case("de: Amoxicillin 250 mg Kautabletten", accepted=[9036], picker_used=True),
    Case("hr: Diazepam 10 mg/2 ml ampule", accepted=[9037]),
    # --- no deterministic match: the search and the reranker get the parsed structure
    Case(
        "hr: Paracetamol 650 mg tbl (no such strength)",
        first_candidate=None,
        note="no drug with these ingredients and strengths and form",
    ),
    Case(
        "hr: Paracetamol 500 mg pilula (form the picker cannot place)",
        _cols("paracetamol", "pilula", amount_value=500, amount_unit="mg"),
        parser_used=True,
    ),
]


class Recording:
    """Reranker that records its first candidate list and notes, and selects nothing."""

    def __init__(self):
        self.candidates = None
        self.notes = None

    async def rerank(self, term, candidates, drug_specific, **kwargs):
        from app.llm.structured import RerankResult

        if self.candidates is None:
            self.candidates = [c["concept_id"] for c in candidates]
            self.notes = kwargs.get("notes") or []
        return RerankResult(None, None)


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
async def test_synthetic_drug(case: Case, drug_vocabulary):
    from app.workflows.service import run_pipeline

    name = case.name.split(": ", 1)[1].split(" (")[0]
    structure = parse_drug_structure(case.columns) if case.columns else None
    parser = FakeParser(PARSED)
    picker = FakeFormPicker(FORMS)
    reranker = Recording()
    result = await run_pipeline(
        drug_state(name, structure),
        drug_ctx(parser, reranker, FakeTranslator(ENGLISH), picker),
        "automap_drug",
    )
    step = next(t for t in result["trace"] if t["node"] == "match_drug")
    shown = step["summary"] + " | " + (result.get("rationale") or "")
    shown += " | " + " | ".join(reranker.notes or [])

    if case.accepted is not None:
        ids = [c["concept_id"] for c in result.get("selected_concepts") or []] or [
            result["selected"]["concept_id"]
        ]
        assert ids == case.accepted, shown
        assert result["method"] == "auto_drug_structured" and result["confidence"] == 10
        assert reranker.candidates is None  # decided without the LLM reranker
    else:
        assert result.get("method") != "auto_drug_structured", shown
        if case.first_candidate is not None:
            assert reranker.candidates and reranker.candidates[0] == case.first_candidate, shown
    assert ("approximate" in step["summary"]) == case.approximate, shown
    if case.note:
        assert case.note in shown, shown
    if case.picker_used is not None:
        assert bool(picker.calls) == case.picker_used, picker.calls
    if case.parser_used is not None:
        assert bool(parser.calls) == case.parser_used, parser.calls
