"""Deterministic drug mapping: structure (provided or parsed) -> drug_strength match."""

import asyncio

from pydantic import BaseModel, ConfigDict, ValidationError

from app.llm.schemas import DrugComponent, DrugStructure
from app.services.drug_matching import DrugMatch
from app.workflows.context import NodeContext
from app.workflows.registry import router
from app.workflows.state import AutomapState

LEVEL_NAMES = {
    "clinical_drug": "Clinical Drug",
    "quant_clinical_drug": "Quant Clinical Drug",
    "clinical_drug_comp": "Clinical Drug Comp",
    "clinical_drug_form": "Clinical Drug Form",
    "ingredient": "Ingredient",
}


class MatchDrugParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Parse free-text names with the LLM when no structure was provided
    parse_free_text: bool = True
    # Confidence given to a unique deterministic match
    confidence: int = 10


def describe(structure: DrugStructure) -> str:
    def strength(c: DrugComponent) -> str:
        if c.amount_value is not None:
            return f" {c.amount_value:g} {c.amount_unit or ''}".rstrip()
        if c.numerator_value is not None:
            den = (
                f"{c.denominator_value:g} {c.denominator_unit or ''}".strip()
                if c.denominator_value
                else (c.denominator_unit or "")
            )
            return f" {c.numerator_value:g} {c.numerator_unit or ''}" + (f"/{den}" if den else "")
        return ""

    parts = " + ".join(f"{c.ingredient}{strength(c)}" for c in structure.components)
    return parts + (f"; {structure.dose_form}" if structure.dose_form else "")


async def _translate_names(
    ctx: NodeContext, structure: DrugStructure, names: list[str]
) -> DrugStructure:
    """Provided structures may use local names (paracetamol, tableta): render them in English."""
    if ctx.translator is None:
        return structure
    english: dict[str, str] = {}
    for name in names:
        try:
            english[name] = (await ctx.translator.translate(name)).english
        except Exception as exc:
            ctx.logger.warning("Could not translate %r: %s", name, exc)
    components = [
        c.model_copy(update={"ingredient": english.get(c.ingredient, c.ingredient)})
        for c in structure.components
    ]
    form = english.get(structure.dose_form or "", structure.dose_form)
    return structure.model_copy(update={"components": components, "dose_form": form})


@router(
    "match_drug",
    routes={"matched", "fallback"},
    params=MatchDrugParams,
    description="Drugs: match ingredients, strengths and dose form deterministically on drug_strength",
)
async def match_drug(state: AutomapState, ctx: NodeContext, params: MatchDrugParams):
    if not state.get("drug_specific"):
        return "fallback", {"_summary": "not a drug vocabulary"}
    if ctx.drug_matcher is None:
        return "fallback", {"_summary": "drug matching unavailable (no drug_strength data)"}

    structure: DrugStructure | None = None
    origin = "provided"
    match = DrugMatch()
    provided = state.get("drug_structure")
    if provided:
        try:
            structure = DrugStructure.model_validate(provided)
        except ValidationError as exc:
            ctx.logger.warning("Invalid drug structure for %s: %s", state.get("source_id"), exc)
        if structure is not None:
            match = await asyncio.to_thread(ctx.drug_matcher, structure)
            if match.unresolved:
                structure = await _translate_names(ctx, structure, match.unresolved)
                match = await asyncio.to_thread(ctx.drug_matcher, structure)

    if (structure is None or (match.level is None and match.unresolved)) and params.parse_free_text:
        if ctx.drug_parser is None:
            return "fallback", {"_summary": "no drug parser configured"}
        hint = describe(structure) if structure else None
        try:
            parsed = await ctx.drug_parser.parse(state.get("source_name", ""), hint)
        except Exception as exc:
            ctx.logger.warning("Drug parsing failed for %r: %s", state.get("source_name"), exc)
            return "fallback", {"_summary": f"parse error ({type(exc).__name__})"}
        if not parsed.is_drug:
            return "fallback", {"_summary": "not a drug product according to the parser"}
        structure = DrugStructure.model_validate(parsed.model_dump(exclude={"is_drug"}))
        origin = "parsed"
        match = await asyncio.to_thread(ctx.drug_matcher, structure)

    if structure is None:
        return "fallback", {"_summary": "no structure"}
    described = describe(structure)
    update: dict = {
        "notes": [f"Parsed drug structure ({origin}): {described}"],
        "meta": {
            "drug_structure": structure.model_dump(exclude_none=True),
            "drug_match": match.reason,
        },
    }
    if match.unique:
        found = await asyncio.to_thread(ctx.get_concepts, match.concept_ids)
        if found:
            level = LEVEL_NAMES.get(match.level or "", match.level)
            update.update(
                selected=found[0],
                confidence=params.confidence,
                rationale=f"Deterministic {level} match on {described} ({origin} structure)",
                method="auto_drug_structured",
                outcome="selected",
                _summary=f"{level} {found[0]['concept_name']!r} for {described}",
            )
            return "matched", update
    if match.concept_ids:
        # Several equally good drugs: let the reranker choose among them
        update["structured_candidates"] = await asyncio.to_thread(
            ctx.get_concepts, match.concept_ids[:30]
        )
    update["_summary"] = f"{described}: {match.reason or 'no match'} ({origin})"
    return "fallback", update
