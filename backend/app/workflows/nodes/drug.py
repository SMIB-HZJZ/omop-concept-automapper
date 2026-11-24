"""Deterministic drug mapping: structure (provided or parsed) -> drug_strength match."""

import asyncio

from pydantic import BaseModel, ConfigDict, ValidationError

from app.llm.schemas import DrugComponent, DrugStructure
from app.services.drug_matching import DrugMatch
from app.workflows.context import NodeContext
from app.workflows.registry import router
from app.workflows.state import AutomapState

LEVEL_NAMES = {
    "branded_drug": "Branded Drug",
    "quant_branded_drug": "Quant Branded Drug",
    "branded_drug_comp": "Branded Drug Comp",
    "branded_drug_form": "Branded Drug Form",
    "clinical_drug": "Clinical Drug",
    "quant_clinical_drug": "Quant Clinical Drug",
    "clinical_drug_comp": "Clinical Drug Comp",
    "clinical_drug_form": "Clinical Drug Form",
    "ingredient": "Ingredient",
}


class MatchDrugParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The step reports what it found in ``meta``: drug_status is one of exact (accepted), tie
    # (several equally good drugs), approximate, none (no drug with this structure), not_drug,
    # parse_error or no_structure; drug_origin, drug_level and drug_search_text describe the match.

    # Parse free-text names with the LLM when no structure was provided
    parse_free_text: bool = True
    # Confidence given to a unique deterministic match
    confidence: int = 10


def _rx_number(value: float) -> str:
    return f"{value:.6g}"


def _rx_unit(unit: str | None) -> str:
    return (unit or "").strip().upper().replace("µ", "MC").replace("MICROGRAM", "MCG")


def search_text(structure: DrugStructure) -> str:
    """The structure written like an RxNorm name (``2 ML diazepam 5 MG/ML Injectable Solution
    [Normabel]``), for a similarity search that ranks RxNorm drugs well: their names follow this
    pattern, the source term often does not (other language, abbreviations)."""
    parts = []
    for c in structure.components:
        strength = ""
        if c.amount_value is not None:
            strength = f" {_rx_number(c.amount_value)} {_rx_unit(c.amount_unit)}".rstrip()
        elif c.numerator_value is not None:
            # RxNorm writes concentrations per one unit (500 mg/5 mL -> 100 MG/ML)
            per = c.denominator_value or 1
            strength = (
                f" {_rx_number(c.numerator_value / per)} {_rx_unit(c.numerator_unit)}"
                + (f"/{_rx_unit(c.denominator_unit)}" if c.denominator_unit else "")
            ).rstrip()
        parts.append(f"{c.ingredient}{strength}")
    text_ = " / ".join(parts)
    if structure.quantity_value is not None:
        text_ = (
            f"{_rx_number(structure.quantity_value)} {_rx_unit(structure.quantity_unit)} {text_}"
        )
    if structure.dose_form:
        text_ += f" {structure.dose_form}"
    if structure.brand_name:
        text_ += f" [{structure.brand_name}]"
    return text_.strip()


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
    if structure.brand_name:
        parts = f"{parts} [{structure.brand_name}]" if parts else f"[{structure.brand_name}]"
    if structure.dose_form:
        parts += f"; {structure.dose_form}"
    if structure.quantity_value is not None:
        parts += f"; quantity {structure.quantity_value:g} {structure.quantity_unit or ''}".rstrip()
    return parts


async def _translate_names(
    ctx: NodeContext, structure: DrugStructure, names: list[str]
) -> DrugStructure:
    """Provided ingredients may use local names (paracetamolo): render them in English."""
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
    return structure.model_copy(update={"components": components})


async def _pick_forms(
    ctx: NodeContext, form: str, product: str | None, unused: bool = False
) -> list[str] | None:
    """RxNorm dose forms for a form term the vocabulary does not know (any language), or, with
    ``unused``, for a listed form no product of this drug uses."""
    if ctx.dose_form_picker is None:
        return None
    try:
        if unused:
            return await ctx.dose_form_picker.pick(form, product, unused=True) or None
        return await ctx.dose_form_picker.pick(form, product) or None
    except Exception as exc:
        ctx.logger.warning("Could not choose a dose form for %r: %s", form, exc)
        return None


async def _match(
    ctx: NodeContext, structure: DrugStructure, product: str | None, translate: bool
) -> DrugMatch:
    """Exact names first; an unknown dose form is rendered as RxNorm forms by the LLM, unknown
    ingredients are translated (``translate``); similar ingredient names last."""
    match = await asyncio.to_thread(ctx.drug_matcher, structure, False, None)
    form_choice = None
    form = structure.dose_form
    if form and form in match.unresolved:
        form_choice = await _pick_forms(ctx, form, product)
        if form_choice:
            match = await asyncio.to_thread(ctx.drug_matcher, structure, False, form_choice)
    ingredients = [n for n in match.unresolved if n != form and n not in match.unresolved_units]
    if ingredients and translate:
        structure = await _translate_names(ctx, structure, ingredients)
        match = await asyncio.to_thread(ctx.drug_matcher, structure, False, form_choice)
    if match.unresolved:
        match = await asyncio.to_thread(ctx.drug_matcher, structure, True, form_choice)
    if match.level is None and not match.unresolved and form and form_choice is None:
        # A generic RxNorm form (Tablet, Syrup) that products rarely use: ask for the usual ones
        choice = await _pick_forms(ctx, form, product, unused=True)
        if choice and {c.lower() for c in choice} != {form.strip().lower()}:
            retry = await asyncio.to_thread(ctx.drug_matcher, structure, True, choice)
            if retry.level is not None:
                match = retry
    match.structure = structure
    return match


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
            match = await _match(ctx, structure, state.get("source_name"), translate=True)
            structure = match.structure

    if (structure is None or (match.level is None and match.unresolved)) and params.parse_free_text:
        if ctx.drug_parser is None:
            return "fallback", {"_summary": "no drug parser configured"}
        hint = describe(structure) if structure else None
        try:
            parsed = await ctx.drug_parser.parse(state.get("source_name", ""), hint)
        except Exception as exc:
            ctx.logger.warning("Drug parsing failed for %r: %s", state.get("source_name"), exc)
            return "fallback", {
                "_summary": f"parse error ({type(exc).__name__})",
                "meta": {"drug_status": "parse_error"},
            }
        if not parsed.is_drug:
            return "fallback", {
                "_summary": "not a drug product according to the parser",
                "meta": {"drug_status": "not_drug"},
            }
        structure = DrugStructure.model_validate(parsed.model_dump(exclude={"is_drug"}))
        origin = "parsed"
        # The parser already writes English INNs; its form is checked like a provided one
        match = await _match(ctx, structure, state.get("source_name"), translate=False)

    if structure is None:
        return "fallback", {"_summary": "no structure", "meta": {"drug_status": "no_structure"}}
    described = describe(structure)
    notes = [f"Parsed drug structure ({origin}): {described}"]
    brand_in_name = None
    if not structure.brand_name and ctx.brand_finder is not None:
        # The structure names no brand: is one written in the term anyway? Then the search keeps
        # branded drugs (see retrieve.skip_branded_without_brand)
        brand_in_name = await asyncio.to_thread(ctx.brand_finder, state.get("source_name", ""))
    if match.resolved:
        notes.append("Names read as: " + "; ".join(match.resolved))
    if match.approximate:
        notes.append("Structured match is approximate: " + "; ".join(match.approximate))
    update: dict = {
        "notes": notes,
        "meta": {
            "drug_structure": structure.model_dump(exclude_none=True),
            "drug_origin": origin,
            "drug_level": match.level,
            "drug_search_text": search_text(match.rxnorm or structure),
            **({"drug_brand_in_name": brand_in_name} if brand_in_name else {}),
            "drug_status": "approximate"
            if match.approximate
            else ("tie" if match.concept_ids else "none"),
            "drug_match": match.reason,
            **({"drug_match_resolved": match.resolved} if match.resolved else {}),
            **({"drug_match_approximate": match.approximate} if match.approximate else {}),
        },
    }
    if match.unique and not match.approximate:
        found = await asyncio.to_thread(ctx.get_concepts, match.concept_ids)
        order = {cid: i for i, cid in enumerate(match.concept_ids)}
        found = sorted(found, key=lambda c: order.get(c["concept_id"], 0))
        if found and (len(found) == 1 or match.combined):
            level = LEVEL_NAMES.get(match.level or "", match.level)
            names = " + ".join(repr(c["concept_name"]) for c in found)
            update.update(
                selected=found[0],
                selected_concepts=found if match.combined else [],
                confidence=params.confidence,
                rationale=(
                    f"Deterministic {level} match on {described} ({origin} structure"
                    + (f"; {match.reason}" if "used the" in match.reason else "")
                    + (f"; {'; '.join(match.resolved)}" if match.resolved else "")
                    + ")"
                ),
                method="auto_drug_structured",
                outcome="selected",
                meta={**update["meta"], "drug_status": "exact"},
                _summary=f"{level} {names} for {described}",
            )
            return "matched", update
    if match.concept_ids:
        # Several equally good drugs, or an approximate match: the reranker chooses among them
        update["structured_candidates"] = await asyncio.to_thread(
            ctx.get_concepts, match.concept_ids[:30]
        )
    approx = f"; approximate: {'; '.join(match.approximate)}" if match.approximate else ""
    update["_summary"] = f"{described}: {match.reason or 'no match'}{approx} ({origin})"
    return "fallback", update
