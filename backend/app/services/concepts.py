"""OMOP concept lookups."""

from collections.abc import Sequence

from sqlalchemy import text
from sqlmodel import Session

CONCEPT_COLUMNS = """
    c.concept_id, c.concept_name, c.concept_class_id, c.domain_id, c.vocabulary_id,
    c.concept_code, c.standard_concept, c.invalid_reason, ca.atc7_codes
"""


def get_concepts(session: Session, concept_ids: Sequence[int]) -> list[dict]:
    if not concept_ids:
        return []
    rows = session.execute(
        text(
            f"""
            SELECT {CONCEPT_COLUMNS}
            FROM concept c LEFT JOIN concept_atc7 ca ON ca.concept_id = c.concept_id
            WHERE c.concept_id = ANY(:ids)
            """
        ),
        {"ids": list(concept_ids)},
    ).mappings()
    by_id = {r["concept_id"]: dict(r) for r in rows}
    return [by_id[i] for i in concept_ids if i in by_id]


def get_ancestors(
    session: Session, concept_id: int, max_levels: int = 2, limit: int = 25
) -> list[dict]:
    rows = session.execute(
        text(
            f"""
            SELECT {CONCEPT_COLUMNS}, a.min_levels_of_separation AS levels
            FROM concept_ancestor a
            JOIN concept c ON c.concept_id = a.ancestor_concept_id
            LEFT JOIN concept_atc7 ca ON ca.concept_id = c.concept_id
            WHERE a.descendant_concept_id = :id
              AND a.min_levels_of_separation BETWEEN 1 AND :levels
            ORDER BY a.min_levels_of_separation, c.concept_name
            LIMIT :limit
            """
        ),
        {"id": concept_id, "levels": max_levels, "limit": limit},
    ).mappings()
    return [dict(r) for r in rows]


def list_domains(session: Session) -> list[str]:
    return list(
        session.execute(text("SELECT domain_id FROM domain_label ORDER BY domain_id")).scalars()
    )
