"""Deterministic drug matching on OMOP ``drug_strength`` (RxNorm and RxNorm Extension).

The match follows the level of detail of the source, as the reranker rules do. With a brand name the
branded level is tried first, then the clinical one:

| source has                          | branded (brand given)  | clinical                                  |
|-------------------------------------|------------------------|-------------------------------------------|
| ingredients + strengths + form      | Branded Drug           | Clinical Drug, else Quant Clinical Drug   |
| ingredient(s) + strengths, no form  | Branded Drug Comp      | Clinical Drug Comp (one per ingredient)   |
| ingredients + form, no strengths    | Branded Drug Form      | Clinical Drug Form                        |
| ingredients only                    | Branded Drug Form (*)  | Clinical Drug Form (*), else the Ingredients |

(*) only when exactly one exists for that ingredient set (any dose form). "One per ingredient" and
"the Ingredients" are combined matches: the source is mapped to all of them together.

A brand without ingredients takes its ingredients from its branded products, when they all share
one ingredient set. The ingredient set must be identical (no extra or missing ingredients),
strengths equal after unit conversion (1 % tolerance) and the dose form identical. Box and marketed
products are never targets. Relationship names follow the OMOP RxNorm conventions
(``RxNorm has dose form``, ``RxNorm has ing``, ``Has brand name``).
"""

import logging
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlmodel import Session

from app.llm.schemas import DrugComponent, DrugStructure
from app.services.drug_units import close, concentration, quantity

logger = logging.getLogger(__name__)

DRUG_VOCABULARIES = ("RxNorm", "RxNorm Extension")


@dataclass
class DrugMatch:
    level: str | None = None  # clinical_drug | quant_clinical_drug | ... | None
    concept_ids: list[int] = field(default_factory=list)
    reason: str = ""
    unresolved: list[str] = field(default_factory=list)  # names that matched no concept
    # True: concept_ids belong together (one mapping per ingredient), not alternatives
    combined: bool = False

    @property
    def unique(self) -> bool:
        return len(self.concept_ids) == 1 or (self.combined and bool(self.concept_ids))


# --------------------------------------------------------------------------- resolution


def resolve_ingredients(session: Session, names: list[str]) -> dict[str, int]:
    """Ingredient name (or concept id as text) -> standard RxNorm/RxNorm Extension ingredient."""
    wanted = {n.strip().lower() for n in names if n and n.strip()}
    ids = [int(n) for n in wanted if n.isdigit()]
    rows = session.execute(
        text(
            """
            SELECT DISTINCT ON (key) key, concept_id FROM (
                SELECT lower(concept_name) AS key, concept_id, vocabulary_id
                FROM concept
                WHERE concept_class_id = 'Ingredient' AND standard_concept = 'S'
                  AND vocabulary_id = ANY(:vocabs) AND lower(concept_name) = ANY(:names)
                UNION ALL
                SELECT concept_id::text, concept_id, vocabulary_id
                FROM concept
                WHERE concept_class_id = 'Ingredient' AND standard_concept = 'S'
                  AND vocabulary_id = ANY(:vocabs) AND concept_id = ANY(:ids)
            ) found
            ORDER BY key, vocabulary_id = 'RxNorm' DESC, concept_id
            """
        ),
        {"names": list(wanted), "ids": ids, "vocabs": list(DRUG_VOCABULARIES)},
    ).all()
    return {key: cid for key, cid in rows}


def resolve_dose_form(session: Session, name: str | None) -> int | None:
    if not name or not name.strip():
        return None
    return session.execute(
        text(
            """
            SELECT concept_id FROM concept
            WHERE concept_class_id = 'Dose Form' AND vocabulary_id = ANY(:vocabs)
              AND invalid_reason IS NULL AND lower(concept_name) = lower(:name)
            ORDER BY vocabulary_id = 'RxNorm' DESC, concept_id
            LIMIT 1
            """
        ),
        {"name": name.strip(), "vocabs": list(DRUG_VOCABULARIES)},
    ).scalar()


def dose_form_names(session: Session) -> list[str]:
    return list(
        session.execute(
            text(
                """
                SELECT DISTINCT concept_name FROM concept
                WHERE concept_class_id = 'Dose Form' AND vocabulary_id = ANY(:vocabs)
                  AND invalid_reason IS NULL
                ORDER BY concept_name
                """
            ),
            {"vocabs": list(DRUG_VOCABULARIES)},
        ).scalars()
    )


def has_drug_strength(session: Session) -> bool:
    return bool(session.execute(text("SELECT EXISTS (SELECT 1 FROM drug_strength)")).scalar())


# --------------------------------------------------------------------------- strengths


def _strength_matches(component: DrugComponent, row: dict) -> bool:
    """Source component vs one drug_strength row (unit codes already joined in)."""
    if component.amount_value is not None:
        source = quantity(component.amount_value, component.amount_unit)
        if source is None:
            return False
        if row["amount_value"] is not None:
            target = quantity(float(row["amount_value"]), row["amount_unit"])
            return (
                target is not None
                and target.dimension == source.dimension
                and close(target.value, source.value)
            )
        # "500 mg" against a per-mL concentration cannot be compared
        return False
    source_c = concentration(
        component.numerator_value,
        component.numerator_unit,
        component.denominator_value,
        component.denominator_unit,
    )
    if source_c is None or row["numerator_value"] is None:
        return False
    target_c = concentration(
        float(row["numerator_value"]),
        row["numerator_unit"],
        float(row["denominator_value"]) if row["denominator_value"] is not None else None,
        row["denominator_unit"],
    )
    return target_c is not None and target_c[1:] == source_c[1:] and close(target_c[0], source_c[0])


_BRAND_FILTER = """
    AND (CAST(:brand AS integer) IS NULL OR EXISTS (
        SELECT 1 FROM concept_relationship rb
        WHERE rb.concept_id_1 = {drug} AND rb.relationship_id = 'Has brand name'
          AND rb.concept_id_2 = :brand AND rb.invalid_reason IS NULL))
"""
_FORM_FILTER = """
    AND (CAST(:form AS integer) IS NULL OR EXISTS (
        SELECT 1 FROM concept_relationship rf
        WHERE rf.concept_id_1 = {drug} AND rf.relationship_id = 'RxNorm has dose form'
          AND rf.concept_id_2 = :form AND rf.invalid_reason IS NULL))
"""


def _strength_rows(
    session: Session,
    ingredient_ids: list[int],
    classes: list[str],
    dose_form_id: int | None,
    brand_id: int | None = None,
) -> dict[int, list[dict]]:
    """drug_strength rows of drugs with exactly these ingredients, by drug concept id."""
    rows = session.execute(
        text(
            """
            WITH candidates AS (
                SELECT ds.drug_concept_id
                FROM drug_strength ds
                WHERE ds.drug_concept_id IN (
                    SELECT drug_concept_id FROM drug_strength WHERE ingredient_concept_id = :first
                )
                GROUP BY ds.drug_concept_id
                HAVING COUNT(DISTINCT ds.ingredient_concept_id) = :n
                   AND bool_and(ds.ingredient_concept_id = ANY(CAST(:ingredients AS integer[])))
            )
            SELECT ds.drug_concept_id, ds.ingredient_concept_id, c.vocabulary_id,
                   ds.amount_value, au.concept_code AS amount_unit,
                   ds.numerator_value, nu.concept_code AS numerator_unit,
                   ds.denominator_value, du.concept_code AS denominator_unit
            FROM candidates
            JOIN drug_strength ds ON ds.drug_concept_id = candidates.drug_concept_id
            JOIN concept c ON c.concept_id = ds.drug_concept_id
            LEFT JOIN concept au ON au.concept_id = ds.amount_unit_concept_id
            LEFT JOIN concept nu ON nu.concept_id = ds.numerator_unit_concept_id
            LEFT JOIN concept du ON du.concept_id = ds.denominator_unit_concept_id
            WHERE c.standard_concept = 'S' AND c.invalid_reason IS NULL
              AND c.vocabulary_id = ANY(:vocabs) AND c.concept_class_id = ANY(:classes)
              AND ds.invalid_reason IS NULL
            """
            + _FORM_FILTER.format(drug="ds.drug_concept_id")
            + _BRAND_FILTER.format(drug="ds.drug_concept_id")
        ),
        {
            "first": ingredient_ids[0],
            "n": len(set(ingredient_ids)),
            "ingredients": ingredient_ids,
            "vocabs": list(DRUG_VOCABULARIES),
            "classes": classes,
            "form": dose_form_id,
            "brand": brand_id,
        },
    ).mappings()
    by_drug: dict[int, list[dict]] = {}
    for r in rows:
        by_drug.setdefault(r["drug_concept_id"], []).append(dict(r))
    return by_drug


def _prefer_rxnorm(vocabulary_of: dict[int, str], matches: list[int]) -> list[int]:
    """RxNorm Extension only fills gaps in RxNorm; when both match, keep RxNorm."""
    rxnorm = [d for d in matches if vocabulary_of[d] == "RxNorm"]
    return sorted(rxnorm if rxnorm else matches)


def _drug_forms(
    session: Session,
    concept_class: str,
    ingredient_ids: list[int],
    dose_form_id: int | None,
    brand_id: int | None = None,
) -> list[int]:
    """Clinical/Branded Drug Forms with exactly these ingredients (and form/brand if given)."""
    rows = session.execute(
        text(
            """
            SELECT c.concept_id, c.vocabulary_id
            FROM concept c
            WHERE c.concept_class_id = :cls AND c.standard_concept = 'S'
              AND c.invalid_reason IS NULL AND c.vocabulary_id = ANY(:vocabs)
              AND (
                  SELECT array_agg(DISTINCT r.concept_id_2 ORDER BY r.concept_id_2)
                  FROM concept_relationship r
                  WHERE r.concept_id_1 = c.concept_id AND r.relationship_id = 'RxNorm has ing'
                    AND r.invalid_reason IS NULL
              ) = CAST(:ingredients AS integer[])
            """
            + _FORM_FILTER.format(drug="c.concept_id")
            + _BRAND_FILTER.format(drug="c.concept_id")
        ),
        {
            "cls": concept_class,
            "vocabs": list(DRUG_VOCABULARIES),
            "form": dose_form_id,
            "brand": brand_id,
            "ingredients": sorted(set(ingredient_ids)),
        },
    ).all()
    return _prefer_rxnorm({cid: voc for cid, voc in rows}, [cid for cid, _ in rows])


def resolve_brand(session: Session, name: str | None) -> int | None:
    if not name or not name.strip():
        return None
    return session.execute(
        text(
            """
            SELECT concept_id FROM concept
            WHERE concept_class_id = 'Brand Name' AND vocabulary_id = ANY(:vocabs)
              AND invalid_reason IS NULL AND lower(concept_name) = lower(:name)
            ORDER BY vocabulary_id = 'RxNorm' DESC, concept_id
            LIMIT 1
            """
        ),
        {"name": name.strip(), "vocabs": list(DRUG_VOCABULARIES)},
    ).scalar()


def brand_ingredient_sets(session: Session, brand_id: int) -> list[list[int]]:
    """Distinct ingredient sets of the brand's products (one set = the brand is unambiguous)."""
    rows = session.execute(
        text(
            """
            SELECT DISTINCT ingredients FROM (
                SELECT array_agg(DISTINCT ds.ingredient_concept_id ORDER BY ds.ingredient_concept_id)
                       AS ingredients
                FROM concept_relationship r
                JOIN drug_strength ds ON ds.drug_concept_id = r.concept_id_1
                WHERE r.concept_id_2 = :brand AND r.relationship_id = 'Has brand name'
                  AND r.invalid_reason IS NULL AND ds.invalid_reason IS NULL
                GROUP BY r.concept_id_1
            ) per_product
            """
        ),
        {"brand": brand_id},
    ).scalars()
    return [list(r) for r in rows]


# --------------------------------------------------------------------------- matching


def _match_strengths(
    session: Session,
    structure: DrugStructure,
    ingredient_ids: list[int],
    levels: list[tuple[str, list[str]]],
    form_id: int | None,
    brand_id: int | None,
) -> DrugMatch | None:
    component_of = dict(zip(ingredient_ids, structure.components, strict=True))
    for level, class_ids in levels:
        by_drug = _strength_rows(session, ingredient_ids, class_ids, form_id, brand_id)
        matches = [
            drug
            for drug, rows in by_drug.items()
            if all(_strength_matches(component_of[r["ingredient_concept_id"]], r) for r in rows)
        ]
        if matches:
            vocabulary_of = {d: rows[0]["vocabulary_id"] for d, rows in by_drug.items()}
            matches = _prefer_rxnorm(vocabulary_of, matches)
            return DrugMatch(level, matches, f"{len(matches)} {class_ids[0]} match(es)")
    return None


def _match_comps(
    session: Session,
    structure: DrugStructure,
    ingredient_ids: list[int],
    concept_class: str,
    level: str,
    brand_id: int | None,
) -> DrugMatch | None:
    """Strength without dose form: one Drug Comp per ingredient, combined."""
    picked = []
    for ingredient_id, component in zip(ingredient_ids, structure.components, strict=True):
        single = DrugStructure(components=[component])
        found = _match_strengths(
            session, single, [ingredient_id], [(level, [concept_class])], None, brand_id
        )
        if found is None or len(found.concept_ids) != 1:
            return None
        picked.append(found.concept_ids[0])
    return DrugMatch(
        level,
        picked,
        f"{concept_class} per ingredient",
        combined=len(picked) > 1,
    )


def _match_level(
    session: Session,
    structure: DrugStructure,
    ingredient_ids: list[int],
    form_id: int | None,
    brand_id: int | None,
) -> DrugMatch | None:
    """One pass at the branded (brand_id set) or clinical level."""
    branded = brand_id is not None
    prefix = "branded" if branded else "clinical"
    kind = "Branded" if branded else "Clinical"
    with_strength = all(c.has_strength for c in structure.components)
    if with_strength and form_id:
        levels = [
            (f"{prefix}_drug", [f"{kind} Drug"]),
            (f"quant_{prefix}_drug", [f"Quant {kind} Drug"]),
        ]
        return _match_strengths(session, structure, ingredient_ids, levels, form_id, brand_id)
    if with_strength:
        return _match_comps(
            session, structure, ingredient_ids, f"{kind} Drug Comp", f"{prefix}_drug_comp", brand_id
        )
    forms = _drug_forms(session, f"{kind} Drug Form", ingredient_ids, form_id, brand_id)
    if form_id and forms:
        return DrugMatch(f"{prefix}_drug_form", forms, f"{len(forms)} {kind} Drug Form match(es)")
    if not form_id and len(forms) == 1:
        # No form in the source, but only one form exists for these ingredients
        return DrugMatch(f"{prefix}_drug_form", forms, f"the only {kind} Drug Form")
    return None


def match_structure(session: Session, structure: DrugStructure) -> DrugMatch:
    brand_id = resolve_brand(session, structure.brand_name)
    notes = []
    if structure.brand_name and brand_id is None:
        notes.append(f"unknown brand {structure.brand_name!r}")
    if not structure.components:
        if brand_id is None:
            return DrugMatch(reason="no ingredients" + (f"; {notes[0]}" if notes else ""))
        sets = brand_ingredient_sets(session, brand_id)
        if len(sets) != 1:
            return DrugMatch(reason=f"brand has {len(sets)} different ingredient sets")
        ids = sets[0]
        structure = structure.model_copy(
            update={"components": [DrugComponent(ingredient=str(i)) for i in ids]}
        )

    names = [c.ingredient for c in structure.components]
    resolved = resolve_ingredients(session, names)
    unresolved = [n for n in names if n.strip().lower() not in resolved]
    form_id = resolve_dose_form(session, structure.dose_form)
    if structure.dose_form and form_id is None:
        unresolved.append(structure.dose_form)
    if unresolved:
        return DrugMatch(reason="unknown ingredient or dose form", unresolved=unresolved)

    ingredient_ids = [resolved[n.strip().lower()] for n in names]
    if len(set(ingredient_ids)) != len(ingredient_ids):
        return DrugMatch(reason="duplicate ingredient")
    with_strength = all(c.has_strength for c in structure.components)

    # Branded first (when the brand is known), then clinical
    for level_brand in ([brand_id] if brand_id else []) + [None]:
        found = _match_level(session, structure, ingredient_ids, form_id, level_brand)
        if found is not None:
            if level_brand is None and brand_id is not None:
                found.reason += "; no branded match, used the clinical level"
            return found

    if not with_strength and not form_id:
        # Ingredients only: map to every ingredient together
        return DrugMatch(
            "ingredient",
            ingredient_ids,
            "ingredient(s) only",
            combined=len(ingredient_ids) > 1,
        )
    what = (
        "strengths and form"
        if with_strength and form_id
        else ("strengths" if with_strength else "form")
    )
    return DrugMatch(
        reason=f"no drug with these ingredients and {what}" + (f"; {notes[0]}" if notes else "")
    )
