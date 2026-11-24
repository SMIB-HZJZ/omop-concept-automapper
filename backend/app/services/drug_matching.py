"""Deterministic drug matching on OMOP ``drug_strength`` (RxNorm and RxNorm Extension).

The match follows the level of detail of the source, as the reranker rules do. With a brand name the
branded level is tried first, then the clinical one:

| source has                          | branded (brand given)  | clinical                                  |
|-------------------------------------|------------------------|-------------------------------------------|
| ingredients + strengths + form      | Branded Drug           | Clinical Drug, else Quant Clinical Drug   |
| ... + quantity (2 mL ampoule)       | Quant Branded Drug     | Quant Clinical Drug, else Clinical Drug   |
| ingredient(s) + strengths, no form  | Branded Drug Comp      | Clinical Drug Comp (one per ingredient)   |
| ingredients + form, no strengths    | Branded Drug Form      | Clinical Drug Form                        |
| ingredients only                    | Branded Drug Form (*)  | Clinical Drug Form (*), else the Ingredients |

(*) only when exactly one exists for that ingredient set (any dose form). "One per ingredient" and
"the Ingredients" are combined matches: the source is mapped to all of them together.

Names are resolved in tiers, most reliable first: the standard Ingredient's name, its synonyms,
non-standard Ingredients and Precise Ingredients (salts) through ``Maps to``, then the INN names of
single-ingredient ATC codes (paracetamol, salbutamol). With ``fuzzy=True`` a name still unknown is
read as the most similar Ingredient name (trigram similarity), when that one is clearly the best.
Dose forms: the RxNorm name itself (case, accents and punctuation ignored); a form in another
language is first rendered as RxNorm forms by the LLM (``form_choice``).

A brand without ingredients takes its ingredients from its branded products, when they all share
one ingredient set. The ingredient set must be identical (no extra or missing ingredients),
strengths equal after unit conversion (1 % tolerance) and the dose form one of the resolved forms.
Box and marketed products are never targets. Relationship names follow the OMOP RxNorm conventions
(``RxNorm has dose form``, ``RxNorm has ing``, ``Has brand name``, ``RxNorm is a``).

A match is *approximate* when it needed a fuzzy name, a related dose form (same RxNorm Dose Form
Group) or compared an amount with the total amount of a Quant drug. Approximate matches are never
accepted on their own: the workflow passes them to the reranker as its first candidates.
"""

import logging
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlmodel import Session

from app.core.cache import cached
from app.llm.schemas import DrugComponent, DrugStructure
from app.services.drug_units import close, concentration, normalise_unit, quantity

logger = logging.getLogger(__name__)

DRUG_VOCABULARIES = ("RxNorm", "RxNorm Extension")

# Fuzzy names: the best Ingredient needs this trigram similarity and this lead over the next one.
# Look-alike drugs score high (prednisone/prednisolone ~0.7), hence the lead and the review.
FUZZY_MIN_SIMILARITY = 0.6
FUZZY_MIN_LEAD = 0.1

VIA = {
    "synonym": "RxNorm synonym",
    "maps_to": "non-standard ingredient, Maps to",
    "atc": "ATC name",
    "fuzzy": "similar name",
}


@dataclass
class DrugMatch:
    level: str | None = None  # clinical_drug | quant_clinical_drug | ... | None
    concept_ids: list[int] = field(default_factory=list)
    reason: str = ""
    unresolved: list[str] = field(default_factory=list)  # names that matched no concept
    # True: concept_ids belong together (one mapping per ingredient), not alternatives
    combined: bool = False
    # Why the match is not exact; such matches go to the reranker instead of being accepted
    approximate: list[str] = field(default_factory=list)
    # How source names were read when not by their exact name, e.g. "paracetamol → acetaminophen"
    resolved: list[str] = field(default_factory=list)
    # Units the matcher cannot read (also in ``unresolved``); the LLM parser rewrites them
    unresolved_units: list[str] = field(default_factory=list)
    # The structure as finally matched (ingredients translated), set by the workflow step
    structure: DrugStructure | None = None
    # The match left out part of the source, named in ``reason``: a stated quantity no drug has
    # (matched the drug without it), or a brand with no matching product (clinical level)
    dropped_quantity: bool = False
    dropped_brand: bool = False
    # The structure in RxNorm names (standard ingredients, the chosen RxNorm dose form), once
    # every name was resolved; used to write a search query like an RxNorm drug name
    rxnorm: DrugStructure | None = None

    @property
    def unique(self) -> bool:
        return len(self.concept_ids) == 1 or (self.combined and bool(self.concept_ids))


# Drug classes that name a brand; left out of a search when the source names none, since they
# outnumber clinical drugs and score almost the same (their name is the clinical name + [Brand])
BRANDED_CLASSES = (
    "Branded Drug",
    "Branded Drug Comp",
    "Branded Drug Form",
    "Quant Branded Drug",
    "Branded Pack",
)


@dataclass(frozen=True)
class Resolved:
    concept_id: int
    via: str  # name | id | synonym | maps_to | atc | fuzzy
    found: str  # name of the standard ingredient it was read as
    score: float | None = None


# --------------------------------------------------------------------------- resolution


@cached("vocab")
def atc_ingredient_names(session: Session) -> dict[str, int]:
    """Normalised INN names of ATC 5th level codes that map to exactly one standard Ingredient."""
    rows = session.execute(
        text(
            """
            SELECT norm_text(split_part(a.concept_name, ';', 1)) AS name,
                   min(t.concept_id) AS target, count(DISTINCT t.concept_id) AS n
            FROM concept a
            JOIN concept_relationship r ON r.concept_id_1 = a.concept_id
             AND r.relationship_id = 'Maps to' AND r.invalid_reason IS NULL
            JOIN concept t ON t.concept_id = r.concept_id_2
            WHERE a.vocabulary_id = 'ATC' AND a.concept_class_id = 'ATC 5th'
              AND t.concept_class_id = 'Ingredient' AND t.standard_concept = 'S'
              AND t.vocabulary_id = ANY(:vocabs)
              AND NOT EXISTS (  -- combinations map to several ingredients
                  SELECT 1 FROM concept_relationship r2
                  WHERE r2.concept_id_1 = a.concept_id AND r2.relationship_id = 'Maps to'
                    AND r2.invalid_reason IS NULL AND r2.concept_id_2 <> r.concept_id_2)
            GROUP BY 1
            """
        ),
        {"vocabs": list(DRUG_VOCABULARIES)},
    ).all()
    # One name used by several codes (salbutamol R03AC02, R03CC02) must mean one ingredient
    return {name: target for name, target, n in rows if n == 1 and name}


def _pick(rows: list[tuple[int, str, str]]) -> tuple[int, str] | None:
    """One target among (concept_id, vocabulary, found name) hits, RxNorm before its Extension."""
    targets = {cid: (voc, found) for cid, voc, found in rows}
    if len(targets) == 1:
        cid = next(iter(targets))
        return cid, targets[cid][1]
    rxnorm = [cid for cid, (voc, _) in targets.items() if voc == "RxNorm"]
    if len(rxnorm) == 1:
        return rxnorm[0], targets[rxnorm[0]][1]
    return None  # ambiguous


def resolve_ingredients(
    session: Session, names: list[str], fuzzy: bool = False
) -> dict[str, Resolved]:
    """Ingredient name (or concept id as text) -> standard RxNorm/RxNorm Extension ingredient.

    Keys are the lower-cased, stripped names."""
    wanted = list(dict.fromkeys(n.strip().lower() for n in names if n and n.strip()))
    if not wanted:
        return {}
    found: dict[str, Resolved] = {}
    ids = [int(n) for n in wanted if n.isdigit()]
    if ids:
        for cid, name in session.execute(
            text(
                """
                SELECT concept_id, concept_name FROM concept
                WHERE concept_class_id = 'Ingredient' AND standard_concept = 'S'
                  AND vocabulary_id = ANY(:vocabs) AND concept_id = ANY(:ids)
                """
            ),
            {"ids": ids, "vocabs": list(DRUG_VOCABULARIES)},
        ).all():
            found[str(cid)] = Resolved(cid, "id", name)

    rows = session.execute(
        text(
            """
            WITH wanted AS (
                SELECT w AS key, norm_text(w) AS n FROM unnest(CAST(:names AS text[])) w
            )
            SELECT w.key, 1 AS tier, c.concept_id, c.vocabulary_id, c.concept_name AS found
            FROM wanted w JOIN concept c ON norm_text(c.concept_name) = w.n
            WHERE c.concept_class_id = 'Ingredient' AND c.standard_concept = 'S'
              AND c.vocabulary_id = ANY(:vocabs)
            UNION ALL
            SELECT w.key, 2, c.concept_id, c.vocabulary_id, c.concept_name
            FROM wanted w JOIN concept_synonym s ON norm_text(s.concept_synonym_name) = w.n
            JOIN concept c ON c.concept_id = s.concept_id
            WHERE c.concept_class_id = 'Ingredient' AND c.standard_concept = 'S'
              AND c.vocabulary_id = ANY(:vocabs)
            UNION ALL
            -- Deprecated ingredients and Precise Ingredients (salts) map to the standard one
            SELECT w.key, 3, t.concept_id, t.vocabulary_id, t.concept_name
            FROM wanted w JOIN concept src ON norm_text(src.concept_name) = w.n
            JOIN concept_relationship r ON r.concept_id_1 = src.concept_id
             AND r.relationship_id = 'Maps to' AND r.invalid_reason IS NULL
            JOIN concept t ON t.concept_id = r.concept_id_2
            WHERE src.concept_class_id IN ('Ingredient', 'Precise Ingredient')
              AND src.standard_concept IS NULL AND src.vocabulary_id = ANY(:vocabs)
              AND t.concept_class_id = 'Ingredient' AND t.standard_concept = 'S'
              AND t.vocabulary_id = ANY(:vocabs)
            """
        ),
        {"names": [n for n in wanted if n not in found], "vocabs": list(DRUG_VOCABULARIES)},
    ).all()
    tiers: dict[str, dict[int, list[tuple[int, str, str]]]] = {}
    for key, tier, cid, voc, name in rows:
        tiers.setdefault(key, {}).setdefault(tier, []).append((cid, voc, name))
    tier_via = {1: "name", 2: "synonym", 3: "maps_to"}
    for key, by_tier in tiers.items():
        for tier in sorted(by_tier):
            picked = _pick(by_tier[tier])
            if picked:
                found[key] = Resolved(picked[0], tier_via[tier], picked[1])
                break

    missing = [n for n in wanted if n not in found]
    if missing:
        atc = atc_ingredient_names(session)
        norms = dict(
            session.execute(
                text("SELECT w, norm_text(w) FROM unnest(CAST(:names AS text[])) w"),
                {"names": missing},
            ).all()
        )
        hits = {n: atc[norms[n]] for n in missing if norms.get(n) in atc}
        if hits:
            name_of = dict(
                session.execute(
                    text(
                        "SELECT concept_id, concept_name FROM concept WHERE concept_id = ANY(:ids)"
                    ),
                    {"ids": list(set(hits.values()))},
                ).all()
            )
            for n, cid in hits.items():
                found[n] = Resolved(cid, "atc", name_of.get(cid, str(cid)))

    if fuzzy:
        for n in [n for n in wanted if n not in found and not n.isdigit()]:
            hit = _fuzzy_ingredient(session, n)
            if hit:
                found[n] = hit
    return found


def _fuzzy_ingredient(session: Session, name: str) -> Resolved | None:
    """The clearly most similar standard Ingredient name (typos, spelling variants)."""
    session.execute(
        text("SELECT set_config('pg_trgm.similarity_threshold', :t, true)"),
        {"t": str(FUZZY_MIN_SIMILARITY - FUZZY_MIN_LEAD)},
    )
    rows = session.execute(
        text(
            """
            SELECT lower(c.concept_name) AS name, c.concept_id, c.vocabulary_id,
                   similarity(lower(c.concept_name), :q) AS score
            FROM concept c
            WHERE c.standard_concept = 'S' AND lower(c.concept_name) % :q
              AND c.concept_class_id = 'Ingredient' AND c.vocabulary_id = ANY(:vocabs)
            ORDER BY score DESC, c.vocabulary_id = 'RxNorm' DESC, c.concept_id
            LIMIT 10
            """
        ),
        {"q": name, "vocabs": list(DRUG_VOCABULARIES)},
    ).all()
    # The same name in RxNorm and its Extension counts once (the first row: RxNorm)
    best: dict[str, tuple[int, float]] = {}
    for lowered, cid, _voc, score in rows:
        best.setdefault(lowered, (cid, float(score)))
    ranked = sorted(best.items(), key=lambda kv: -kv[1][1])
    if not ranked or ranked[0][1][1] < FUZZY_MIN_SIMILARITY:
        return None
    if len(ranked) > 1 and ranked[0][1][1] - ranked[1][1][1] < FUZZY_MIN_LEAD:
        return None
    lowered, (cid, score) = ranked[0]
    return Resolved(cid, "fuzzy", lowered, round(score, 2))


def _form_name(session: Session, forms: list[int]) -> str | None:
    """The vocabulary's spelling of the first dose form (the source may write 'oral tablet')."""
    if not forms:
        return None
    return session.execute(
        text("SELECT concept_name FROM concept WHERE concept_id = :id"), {"id": forms[0]}
    ).scalar()


def resolve_dose_form(session: Session, names: list[str]) -> list[int]:
    """Valid RxNorm/RxNorm Extension dose forms with one of these names."""
    names = [n.strip() for n in names if n and n.strip()]
    if not names:
        return []
    return list(
        session.execute(
            text(
                """
                SELECT concept_id FROM concept
                WHERE concept_class_id = 'Dose Form' AND vocabulary_id = ANY(:vocabs)
                  AND invalid_reason IS NULL
                  AND norm_text(concept_name) IN (
                      SELECT norm_text(n) FROM unnest(CAST(:names AS text[])) n)
                ORDER BY vocabulary_id = 'RxNorm' DESC, concept_id
                """
            ),
            {"names": names, "vocabs": list(DRUG_VOCABULARIES)},
        ).scalars()
    )


def dose_form_groups(session: Session, form_ids: list[int]) -> list[tuple[str, list[int]]]:
    """RxNorm Dose Form Groups of these forms with their member forms, smallest group first."""
    rows = session.execute(
        text(
            """
            SELECT g.concept_name, array_agg(DISTINCT m.concept_id_1) AS members
            FROM concept_relationship r
            JOIN concept g ON g.concept_id = r.concept_id_2 AND g.concept_class_id = 'Dose Form Group'
            JOIN concept_relationship m ON m.concept_id_2 = g.concept_id
             AND m.relationship_id = 'RxNorm is a' AND m.invalid_reason IS NULL
            WHERE r.concept_id_1 = ANY(:forms) AND r.relationship_id = 'RxNorm is a'
              AND r.invalid_reason IS NULL
            GROUP BY g.concept_id, g.concept_name
            ORDER BY count(DISTINCT m.concept_id_1), g.concept_name
            """
        ),
        {"forms": form_ids},
    ).all()
    return [(name, [m for m in members if m not in form_ids]) for name, members in rows]


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


def _component_quality(component: DrugComponent, row: dict) -> str | None:
    """Source component vs one drug_strength row: "exact", "total" (an amount compared with the
    total amount of a Quant drug) or None."""
    if component.amount_value is not None:
        source = quantity(component.amount_value, component.amount_unit)
        if source is None:
            return None
        if row["amount_value"] is not None:
            target = quantity(float(row["amount_value"]), row["amount_unit"])
            same = target is not None and target.dimension == source.dimension
            return "exact" if same and close(target.value, source.value) else None
        if row["numerator_value"] is not None and row["denominator_value"] is not None:
            # Quant drug: numerator_value is the total amount in denominator_value (10 mg in 2 mL)
            total = quantity(float(row["numerator_value"]), row["numerator_unit"])
            same = total is not None and total.dimension == source.dimension
            return "total" if same and close(total.value, source.value) else None
        # "500 mg" against a per-mL concentration cannot be compared
        return None
    source_c = concentration(
        component.numerator_value,
        component.numerator_unit,
        component.denominator_value,
        component.denominator_unit,
    )
    if source_c is None or row["numerator_value"] is None:
        return None
    target_c = concentration(
        float(row["numerator_value"]),
        row["numerator_unit"],
        float(row["denominator_value"]) if row["denominator_value"] is not None else None,
        row["denominator_unit"],
    )
    if target_c is not None and target_c[1:] == source_c[1:] and close(target_c[0], source_c[0]):
        return "exact"
    return None


def _drug_quality(
    structure: DrugStructure, component_of: dict[int, DrugComponent], rows: list[dict]
) -> str | None:
    """One drug's rows vs the source: "exact", an approximate kind ("total", "volume", see
    APPROXIMATE_REASONS) or None."""
    qualities = [_component_quality(component_of[r["ingredient_concept_id"]], r) for r in rows]
    if None in qualities:
        return None
    quant = rows[0]["denominator_value"] is not None
    quantity_matches = False
    if structure.quantity_value is not None and quant:
        source = quantity(structure.quantity_value, structure.quantity_unit)
        target = quantity(float(rows[0]["denominator_value"]), rows[0]["denominator_unit"])
        if source is None or target is None or source.dimension != target.dimension:
            return None
        if not close(source.value, target.value):
            return None  # another volume
        quantity_matches = True
    if "total" in qualities and not quantity_matches:
        return "total"  # 10 mg could be in 1 mL or in 2 mL
    if quant and structure.quantity_value is None:
        return "volume"  # 5 mg/mL in a 2 mL ampoule, but the source may be a 10 mL vial
    return "exact"


# Why a drug matched only approximately, by the kind _drug_quality returns
APPROXIMATE_REASONS = {
    "total": "an amount without a quantity compared with the total amount of quantified drugs "
    "(the volume is unknown)",
    "volume": "a concentration without a quantity matched only quantified drugs "
    "(the volume of the source is unknown)",
}


_BRAND_FILTER = """
    AND (CAST(:brand AS integer) IS NULL OR EXISTS (
        SELECT 1 FROM concept_relationship rb
        WHERE rb.concept_id_1 = {drug} AND rb.relationship_id = 'Has brand name'
          AND rb.concept_id_2 = :brand AND rb.invalid_reason IS NULL))
"""
_FORM_FILTER = """
    AND (CAST(:forms AS integer[]) IS NULL OR EXISTS (
        SELECT 1 FROM concept_relationship rf
        WHERE rf.concept_id_1 = {drug} AND rf.relationship_id = 'RxNorm has dose form'
          AND rf.concept_id_2 = ANY(CAST(:forms AS integer[])) AND rf.invalid_reason IS NULL))
"""


def _strength_rows(
    session: Session,
    ingredient_ids: list[int],
    classes: list[str],
    forms: list[int] | None,
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
            "forms": forms or None,
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
    forms: list[int] | None,
    brand_id: int | None = None,
) -> list[int]:
    """Clinical/Branded Drug Forms with exactly these ingredients (and form/brand if given).

    Only drugs with the first ingredient are compared (an indexed lookup), not every Drug Form:
    comparing each one's ingredient list took about half a second per call."""
    ingredients = sorted(set(ingredient_ids))
    rows = session.execute(
        text(
            """
            SELECT c.concept_id, c.vocabulary_id
            FROM concept c
            JOIN (
                SELECT DISTINCT concept_id_1 FROM concept_relationship
                WHERE concept_id_2 = :first AND relationship_id = 'RxNorm has ing'
                  AND invalid_reason IS NULL
            ) with_first ON with_first.concept_id_1 = c.concept_id
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
            "forms": forms or None,
            "brand": brand_id,
            "ingredients": ingredients,
            "first": ingredients[0],
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
              AND invalid_reason IS NULL AND norm_text(concept_name) = norm_text(:name)
            ORDER BY vocabulary_id = 'RxNorm' DESC, concept_id
            LIMIT 1
            """
        ),
        {"name": name.strip(), "vocabs": list(DRUG_VOCABULARIES)},
    ).scalar()


def brand_in_name(session: Session, name: str | None, max_words: int = 4) -> str | None:
    """An RxNorm brand name written in the source term (``Lekadol 500 mg tablete``), or None.

    Used when the structure names no brand, before leaving branded drugs out of a search: a CSV
    may leave the brand column empty and the parser may miss a brand. Brand names that are also
    an ingredient name (``Paracetamol``) do not count; the longest brand found wins."""
    if not name or not name.strip():
        return None
    normalised = session.execute(text("SELECT norm_text(:n)"), {"n": name}).scalar() or ""
    words = normalised.split()
    grams = {
        " ".join(words[i : i + n])
        for n in range(1, max_words + 1)
        for i in range(len(words) - n + 1)
    }
    # Numbers and very short words are strengths and units, not brands
    grams = {g for g in grams if len(g) >= 3 and not g.replace(" ", "").isdigit()}
    if not grams:
        return None
    return session.execute(
        text(
            """
            SELECT b.concept_name FROM concept b
            WHERE b.concept_class_id = 'Brand Name' AND b.vocabulary_id = ANY(:vocabs)
              AND b.invalid_reason IS NULL
              AND norm_text(b.concept_name) = ANY(CAST(:grams AS text[]))
              AND NOT EXISTS (
                  SELECT 1 FROM concept i
                  WHERE i.concept_class_id IN ('Ingredient', 'Precise Ingredient')
                    AND i.vocabulary_id = ANY(:vocabs)
                    AND norm_text(i.concept_name) = norm_text(b.concept_name))
            ORDER BY length(b.concept_name) DESC, b.vocabulary_id = 'RxNorm' DESC
            LIMIT 1
            """
        ),
        {"grams": sorted(grams), "vocabs": list(DRUG_VOCABULARIES)},
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
    forms: list[int] | None,
    brand_id: int | None,
) -> DrugMatch | None:
    """The first level with exact matches; else the first level with approximate ones."""
    component_of = dict(zip(ingredient_ids, structure.components, strict=True))
    rough: DrugMatch | None = None
    for level, class_ids in levels:
        by_drug = _strength_rows(session, ingredient_ids, class_ids, forms, brand_id)
        exact, approx, kinds = [], [], []
        for drug, rows in by_drug.items():
            quality = _drug_quality(structure, component_of, rows)
            if quality == "exact":
                exact.append(drug)
            elif quality is not None:
                approx.append(drug)
                kinds.append(quality)
        vocabulary_of = {d: rows[0]["vocabulary_id"] for d, rows in by_drug.items()}
        if exact:
            exact = _prefer_rxnorm(vocabulary_of, exact)
            return DrugMatch(level, exact, f"{len(exact)} {class_ids[0]} match(es)")
        if approx and rough is None:
            approx = _prefer_rxnorm(vocabulary_of, approx)
            rough = DrugMatch(
                level,
                approx,
                f"{len(approx)} {class_ids[0]} of unknown volume",
                approximate=[APPROXIMATE_REASONS[k] for k in dict.fromkeys(kinds)],
            )
    return rough


def _match_comps(
    session: Session,
    structure: DrugStructure,
    ingredient_ids: list[int],
    concept_class: str,
    level: str,
    brand_id: int | None,
) -> DrugMatch | None:
    """Strength without dose form: one Drug Comp per ingredient, combined. When an ingredient has
    several equal Drug Comps, they are all returned as alternatives for the reranker."""
    picked: list[list[int]] = []
    approximate: list[str] = []
    for ingredient_id, component in zip(ingredient_ids, structure.components, strict=True):
        single = DrugStructure(components=[component])
        found = _match_strengths(
            session, single, [ingredient_id], [(level, [concept_class])], None, brand_id
        )
        if found is None:
            return None
        picked.append(found.concept_ids)
        approximate += found.approximate
    if all(len(ids) == 1 for ids in picked):
        ids = [ids[0] for ids in picked]
        return DrugMatch(
            level,
            ids,
            f"{concept_class} per ingredient",
            combined=len(ids) > 1,
            approximate=approximate,
        )
    alternatives = sorted({i for ids in picked for i in ids})
    return DrugMatch(
        level,
        alternatives,
        f"several {concept_class} for one ingredient",
        approximate=approximate,
    )


def _match_level(
    session: Session,
    structure: DrugStructure,
    ingredient_ids: list[int],
    forms: list[int],
    brand_id: int | None,
) -> DrugMatch | None:
    """One pass at the branded (brand_id set) or clinical level."""
    branded = brand_id is not None
    prefix = "branded" if branded else "clinical"
    kind = "Branded" if branded else "Clinical"
    with_strength = all(c.has_strength for c in structure.components)
    if with_strength and forms:
        plain = (f"{prefix}_drug", [f"{kind} Drug"])
        quant = (f"quant_{prefix}_drug", [f"Quant {kind} Drug"])
        with_quantity = structure.quantity_value is not None
        levels = [quant, plain] if with_quantity else [plain, quant]
        found = _match_strengths(session, structure, ingredient_ids, levels, forms, brand_id)
        if found and with_quantity and found.level == plain[0]:
            found.reason += "; no drug with this quantity, used the one without"
            found.dropped_quantity = True
        return found
    if with_strength:
        return _match_comps(
            session, structure, ingredient_ids, f"{kind} Drug Comp", f"{prefix}_drug_comp", brand_id
        )
    found_forms = _drug_forms(session, f"{kind} Drug Form", ingredient_ids, forms, brand_id)
    if forms and found_forms:
        return DrugMatch(
            f"{prefix}_drug_form", found_forms, f"{len(found_forms)} {kind} Drug Form match(es)"
        )
    if not forms and len(found_forms) == 1:
        # No form in the source, but only one form exists for these ingredients
        return DrugMatch(f"{prefix}_drug_form", found_forms, f"the only {kind} Drug Form")
    return None


def _match_levels(
    session: Session,
    structure: DrugStructure,
    ingredient_ids: list[int],
    forms: list[int],
    brand_id: int | None,
) -> DrugMatch | None:
    """Branded first (when the brand is known), then clinical. When the branded drug lacks the
    stated quantity but a clinical one has it, neither keeps every attribute: both are returned as
    alternatives for the reranker."""
    branded = (
        _match_level(session, structure, ingredient_ids, forms, brand_id) if brand_id else None
    )
    if branded is not None and not branded.dropped_quantity:
        return branded
    clinical = _match_level(session, structure, ingredient_ids, forms, None)
    if branded is None:
        if clinical is not None and brand_id is not None:
            clinical.reason += "; no branded match, used the clinical level"
            clinical.dropped_brand = True
        return clinical
    if clinical is not None and clinical.level and clinical.level.startswith("quant_"):
        return DrugMatch(
            branded.level,
            sorted(set(branded.concept_ids + clinical.concept_ids)),
            "branded drug without the quantity or clinical drug with it",
            approximate=branded.approximate + clinical.approximate,
        )
    return branded


def match_structure(
    session: Session,
    structure: DrugStructure,
    fuzzy: bool = False,
    form_choice: list[str] | None = None,
) -> DrugMatch:
    """``form_choice``: RxNorm dose forms the LLM chose for ``structure.dose_form``, used instead
    of its own name."""
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

    units = [
        u
        for c in structure.components
        for u in (c.amount_unit, c.numerator_unit, c.denominator_unit)
        if u and u.strip() and normalise_unit(u) is None
    ]
    if structure.quantity_unit and normalise_unit(structure.quantity_unit) is None:
        units.append(structure.quantity_unit)
    units = list(dict.fromkeys(units))

    names = [c.ingredient for c in structure.components]
    resolved = resolve_ingredients(session, names, fuzzy=fuzzy)
    unresolved = [n for n in names if n.strip().lower() not in resolved]
    form_names = form_choice if form_choice else [structure.dose_form or ""]
    forms = resolve_dose_form(session, form_names) if structure.dose_form else []
    if structure.dose_form and not forms:
        unresolved.append(structure.dose_form)
    if unresolved or units:
        return DrugMatch(
            reason="unknown "
            + ", ".join(
                w
                for w, found in (("ingredient or dose form", unresolved), ("unit", units))
                if found
            ),
            unresolved=unresolved + units,
            unresolved_units=units,
        )

    readings = [resolved[n.strip().lower()] for n in names]
    resolved_notes = [
        f"{n} → {r.found} ({VIA[r.via]}" + (f" {r.score:.2f})" if r.score is not None else ")")
        for n, r in zip(names, readings, strict=True)
        if r.via in VIA
    ]
    if form_choice:
        resolved_notes.append(
            f"{structure.dose_form} → {' / '.join(form_choice)} (dose form chosen by the LLM)"
        )
    approximate = [
        f"ingredient {n!r} read as {r.found!r} by similarity {r.score:.2f}"
        for n, r in zip(names, readings, strict=True)
        if r.via == "fuzzy"
    ]
    rxnorm = structure.model_copy(
        update={
            "components": [
                c.model_copy(update={"ingredient": r.found})
                for c, r in zip(structure.components, readings, strict=True)
            ],
            "dose_form": form_choice[0] if form_choice else _form_name(session, forms),
        }
    )
    ingredient_ids = [r.concept_id for r in readings]
    if len(set(ingredient_ids)) != len(ingredient_ids):
        return DrugMatch(reason="duplicate ingredient", resolved=resolved_notes, rxnorm=rxnorm)
    with_strength = all(c.has_strength for c in structure.components)

    found = _match_levels(session, structure, ingredient_ids, forms, brand_id)
    if found is None and forms:
        # No drug in the stated form: try the related forms of its smallest Dose Form Group
        for group, members in dose_form_groups(session, forms):
            if not members:
                continue
            found = _match_levels(session, structure, ingredient_ids, members, brand_id)
            if found is not None:
                found.approximate.append(
                    f"no drug in the form {structure.dose_form!r}; related forms ({group})"
                )
                break
    if found is None and not with_strength and not forms:
        # Ingredients only: map to every ingredient together
        found = DrugMatch(
            "ingredient",
            ingredient_ids,
            "ingredient(s) only",
            combined=len(ingredient_ids) > 1,
        )
    if found is not None:
        found.approximate = approximate + found.approximate
        found.resolved = resolved_notes
        found.rxnorm = rxnorm
        return found
    what = (
        "strengths and form"
        if with_strength and forms
        else ("strengths" if with_strength else "form")
    )
    return DrugMatch(
        reason=f"no drug with these ingredients and {what}" + (f"; {notes[0]}" if notes else ""),
        resolved=resolved_notes,
        rxnorm=rxnorm,
    )
