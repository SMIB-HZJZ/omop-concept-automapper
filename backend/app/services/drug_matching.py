"""Deterministic drug matching on OMOP ``drug_strength`` (RxNorm and RxNorm Extension).

The match follows the level of detail of the source, as the reranker rules do:

| source has                       | target class                               |
|----------------------------------|--------------------------------------------|
| ingredients + strengths + form   | Clinical Drug (else Quant Clinical Drug)    |
| one ingredient + strength        | Clinical Drug Comp                          |
| ingredients + form, no strengths | Clinical Drug Form                          |
| one ingredient only              | Ingredient                                  |

The ingredient set must be identical (no extra or missing ingredients), strengths equal after unit
conversion (1 % tolerance) and the dose form identical. Box and marketed products are never
targets. Branded drugs are not matched yet (brand names are left to the fallback pipeline).
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

    @property
    def unique(self) -> bool:
        return len(self.concept_ids) == 1


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


def _strength_rows(
    session: Session, ingredient_ids: list[int], classes: list[str], dose_form_id: int | None
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
              AND (CAST(:form AS integer) IS NULL OR EXISTS (
                  SELECT 1 FROM concept_relationship r
                  WHERE r.concept_id_1 = ds.drug_concept_id
                    AND r.relationship_id = 'RxNorm has dose form'
                    AND r.concept_id_2 = :form AND r.invalid_reason IS NULL
              ))
            """
        ),
        {
            "first": ingredient_ids[0],
            "n": len(set(ingredient_ids)),
            "ingredients": ingredient_ids,
            "vocabs": list(DRUG_VOCABULARIES),
            "classes": classes,
            "form": dose_form_id,
        },
    ).mappings()
    by_drug: dict[int, list[dict]] = {}
    for r in rows:
        by_drug.setdefault(r["drug_concept_id"], []).append(dict(r))
    return by_drug


def _prefer_rxnorm(by_drug: dict[int, list[dict]], matches: list[int]) -> list[int]:
    """RxNorm Extension only fills gaps in RxNorm; when both match, keep RxNorm."""
    rxnorm = [d for d in matches if by_drug[d][0]["vocabulary_id"] == "RxNorm"]
    return rxnorm if rxnorm else matches


def _clinical_drug_forms(
    session: Session, ingredient_ids: list[int], dose_form_id: int
) -> list[int]:
    return list(
        session.execute(
            text(
                """
                SELECT c.concept_id
                FROM concept c
                WHERE c.concept_class_id = 'Clinical Drug Form' AND c.standard_concept = 'S'
                  AND c.vocabulary_id = ANY(:vocabs)
                  AND EXISTS (
                      SELECT 1 FROM concept_relationship r
                      WHERE r.concept_id_1 = c.concept_id AND r.relationship_id = 'RxNorm has dose form'
                        AND r.concept_id_2 = :form AND r.invalid_reason IS NULL)
                  AND (
                      SELECT array_agg(DISTINCT r.concept_id_2 ORDER BY r.concept_id_2)
                      FROM concept_relationship r
                      WHERE r.concept_id_1 = c.concept_id AND r.relationship_id = 'RxNorm has ing'
                        AND r.invalid_reason IS NULL
                  ) = CAST(:ingredients AS integer[])
                ORDER BY c.vocabulary_id = 'RxNorm' DESC, c.concept_id
                """
            ),
            {
                "vocabs": list(DRUG_VOCABULARIES),
                "form": dose_form_id,
                "ingredients": sorted(set(ingredient_ids)),
            },
        ).scalars()
    )


# --------------------------------------------------------------------------- matching


def match_structure(session: Session, structure: DrugStructure) -> DrugMatch:
    if not structure.components:
        return DrugMatch(reason="no ingredients")
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

    if with_strength:
        classes: list[tuple[str, list[str]]] = (
            [("clinical_drug", ["Clinical Drug"]), ("quant_clinical_drug", ["Quant Clinical Drug"])]
            if form_id
            else (
                [("clinical_drug_comp", ["Clinical Drug Comp"])] if len(ingredient_ids) == 1 else []
            )
        )
        for level, class_ids in classes:
            by_drug = _strength_rows(session, ingredient_ids, class_ids, form_id)
            component_of = dict(zip(ingredient_ids, structure.components, strict=True))
            matches = [
                drug
                for drug, rows in by_drug.items()
                if all(_strength_matches(component_of[r["ingredient_concept_id"]], r) for r in rows)
            ]
            if matches:
                matches = sorted(_prefer_rxnorm(by_drug, matches))
                return DrugMatch(level, matches, f"{len(matches)} {class_ids[0]} match(es)")
        return DrugMatch(reason="no drug with these ingredients, strengths and form")

    if form_id:
        forms = _clinical_drug_forms(session, ingredient_ids, form_id)
        if forms:
            return DrugMatch(
                "clinical_drug_form", forms, f"{len(forms)} Clinical Drug Form match(es)"
            )
        return DrugMatch(reason="no Clinical Drug Form with these ingredients and form")
    if len(ingredient_ids) == 1:
        return DrugMatch(
            "ingredient", ingredient_ids, "ingredient only (no strength or form given)"
        )
    return DrugMatch(reason="several ingredients without strength or form")
