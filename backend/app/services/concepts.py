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


# Hierarchy links are shown as parents/children, not again under "related"
HIERARCHY_RELATIONSHIPS = ("Is a", "Subsumes")
RELATIVES_LIMIT = 50
RELATED_LIMIT = 150


def _relatives(session: Session, concept_id: int, direction: str) -> tuple[list[dict], int]:
    """Direct parents ("up") or children ("down") from concept_ancestor, and their total count."""
    own, other = (
        ("descendant_concept_id", "ancestor_concept_id")
        if direction == "up"
        else ("ancestor_concept_id", "descendant_concept_id")
    )
    where = f"a.{own} = :id AND a.min_levels_of_separation = 1"
    rows = session.execute(
        text(
            f"""
            SELECT {CONCEPT_COLUMNS}
            FROM concept_ancestor a
            JOIN concept c ON c.concept_id = a.{other}
            LEFT JOIN concept_atc7 ca ON ca.concept_id = c.concept_id
            WHERE {where}
            ORDER BY c.concept_name
            LIMIT :limit
            """
        ),
        {"id": concept_id, "limit": RELATIVES_LIMIT},
    ).mappings()
    found = [dict(r) for r in rows]
    total = (
        session.execute(
            text(f"SELECT COUNT(*) FROM concept_ancestor a WHERE {where}"), {"id": concept_id}
        ).scalar_one()
        if len(found) == RELATIVES_LIMIT
        else len(found)
    )
    return found, int(total)


def get_details(session: Session, concept_id: int) -> dict | None:
    """A concept with its synonyms, direct parents and children, and other relationships."""
    found = get_concepts(session, [concept_id])
    if not found:
        return None
    synonyms = list(
        session.execute(
            text(
                """
                SELECT DISTINCT concept_synonym_name FROM concept_synonym
                WHERE concept_id = :id AND concept_synonym_name <> :name
                ORDER BY concept_synonym_name LIMIT 25
                """
            ),
            {"id": concept_id, "name": found[0]["concept_name"]},
        ).scalars()
    )
    parents, parent_count = _relatives(session, concept_id, "up")
    children, child_count = _relatives(session, concept_id, "down")
    related = session.execute(
        text(
            f"""
            SELECT r.relationship_id, {CONCEPT_COLUMNS}
            FROM concept_relationship r
            JOIN concept c ON c.concept_id = r.concept_id_2
            LEFT JOIN concept_atc7 ca ON ca.concept_id = c.concept_id
            WHERE r.concept_id_1 = :id AND r.invalid_reason IS NULL
              AND r.concept_id_2 <> :id AND r.relationship_id <> ALL(:hierarchy)
            ORDER BY r.relationship_id, c.concept_name
            LIMIT :limit
            """
        ),
        {"id": concept_id, "hierarchy": list(HIERARCHY_RELATIONSHIPS), "limit": RELATED_LIMIT},
    ).mappings()
    return {
        "concept": found[0],
        "synonyms": synonyms,
        "parents": parents,
        "parent_count": parent_count,
        "children": children,
        "child_count": child_count,
        "related": [dict(r) for r in related],
    }


def list_domains(session: Session) -> list[str]:
    return list(
        session.execute(text("SELECT domain_id FROM domain_label ORDER BY domain_id")).scalars()
    )
