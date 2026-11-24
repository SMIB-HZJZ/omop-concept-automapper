"""Every automatic pick states how it relates to the source term: the drug structure match derives
it from the matched level, the agent states it."""

from app.llm.schemas import DrugComponent, DrugStructure
from app.workflows.nodes.drug import drug_match_type
from app.workflows.service import run_pipeline
from tests.workflows.helpers import FakeSearcher, make_context
from tests.workflows.test_refine_agent import LowConfidenceReranker, _state, scripted_agent


def _drug(strength=True, form=True, quantity=False, brand=False) -> DrugStructure:
    component = DrugComponent(
        ingredient="diazepam", **({"amount_value": 10, "amount_unit": "mg"} if strength else {})
    )
    return DrugStructure(
        components=[component],
        dose_form="Oral Tablet" if form else None,
        quantity_value=2 if quantity else None,
        quantity_unit="mL" if quantity else None,
        brand_name="Normabel" if brand else None,
    )


def test_drug_match_type_follows_the_level():
    assert drug_match_type(_drug(), "clinical_drug") == "exact"
    assert drug_match_type(_drug(brand=True), "branded_drug") == "exact"
    assert drug_match_type(_drug(quantity=True), "quant_clinical_drug") == "exact"
    assert drug_match_type(_drug(form=False), "clinical_drug_comp") == "exact"
    assert drug_match_type(_drug(strength=False, form=False), "ingredient") == "exact"
    # The brand or quantity was dropped: the drug is more general than the source
    assert drug_match_type(_drug(brand=True), "clinical_drug") == "broader"
    assert drug_match_type(_drug(quantity=True), "clinical_drug") == "broader"
    # The only dose form of an ingredient-only source: more specific
    assert drug_match_type(_drug(strength=False, form=False), "clinical_drug_form") == "narrower"
    assert drug_match_type(_drug(brand=True, form=False), "clinical_drug_form") == "related"
    assert drug_match_type(_drug(), None) is None


async def test_agent_states_the_match_type():
    ctx = make_context(
        LowConfidenceReranker(),
        searcher=FakeSearcher(),
        agent_model=scripted_agent(
            {
                "concept_id": 3,
                "confidence": 9,
                "match_type": "broader",
                "rationale": "paracetamol = acetaminophen",
            }
        ),
    )
    result = await run_pipeline(_state(), ctx, "automap")
    assert result["method"] == "auto_agentic"
    assert result["match_type"] == "broader"
