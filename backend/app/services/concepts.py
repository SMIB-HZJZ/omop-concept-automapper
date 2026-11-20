"""OMOP concept lookups."""

import re
from collections.abc import Sequence

from sqlalchemy import text
from sqlmodel import Session

from app.core import cache
from app.vector.store import EXCLUDED_CLASS_FILTER, _ident

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


# Relationships that describe what a concept is (SNOMED "Has finding site", LOINC "Has component",
# RxNorm "RxNorm has ing", ...) contain "has"; the rest (Maps to, Mapped from, Concept replaced
# by, ...) is vocabulary bookkeeping
_DESCRIBES = re.compile(r"(?i)(^|\s)has\s")


# ... except SNOMED's own bookkeeping
_NOT_DESCRIBING = ("Has Module", "Has status")
# English synonyms only (OMOP language concept for English)
ENGLISH = 4180186


def _describes(relationship_id: str) -> bool:
    return bool(_DESCRIBES.search(relationship_id)) and relationship_id not in _NOT_DESCRIBING


def candidate_context(session: Session, concept_ids: Sequence[int]) -> dict[int, dict]:
    """For each concept: up to 3 direct parents, 8 defining attributes and 3 synonyms, in one
    query each, for the AI steps that decide on unsure terms."""
    ids = list(dict.fromkeys(concept_ids))
    if not ids:
        return {}
    context: dict[int, dict] = {i: {"parents": [], "attributes": [], "synonyms": []} for i in ids}
    parents = session.execute(
        text(
            """
            SELECT x.descendant_concept_id AS id, c.concept_name AS name
            FROM concept_ancestor x JOIN concept c ON c.concept_id = x.ancestor_concept_id
            WHERE x.descendant_concept_id = ANY(:ids) AND x.min_levels_of_separation = 1
            ORDER BY x.descendant_concept_id, c.concept_name
            """
        ),
        {"ids": ids},
    ).mappings()
    for r in parents:
        if len(context[r["id"]]["parents"]) < 3:
            context[r["id"]]["parents"].append(r["name"])
    attributes = session.execute(
        text(
            """
            SELECT r.concept_id_1 AS id, r.relationship_id, c.concept_name AS name
            FROM concept_relationship r JOIN concept c ON c.concept_id = r.concept_id_2
            WHERE r.concept_id_1 = ANY(:ids) AND r.invalid_reason IS NULL
              AND r.relationship_id ~* '(^|\\s)has\\s'
              AND r.relationship_id <> ALL(:not_describing)
              AND r.concept_id_2 <> r.concept_id_1
            ORDER BY r.concept_id_1, r.relationship_id, c.concept_name
            """
        ),
        {"ids": ids, "not_describing": list(_NOT_DESCRIBING)},
    ).mappings()
    for r in attributes:
        if len(context[r["id"]]["attributes"]) < 8:
            context[r["id"]]["attributes"].append((r["relationship_id"], r["name"]))
    synonyms = session.execute(
        text(
            """
            SELECT DISTINCT s.concept_id AS id, s.concept_synonym_name AS name
            FROM concept_synonym s JOIN concept c ON c.concept_id = s.concept_id
            WHERE s.concept_id = ANY(:ids) AND s.language_concept_id = :english
              AND lower(s.concept_synonym_name) <> lower(c.concept_name)
              -- not the fully specified name, e.g. "Osteonecrosis (disorder)"
              AND lower(s.concept_synonym_name) NOT LIKE lower(c.concept_name) || ' (%)'
            ORDER BY 1, 2
            """
        ),
        {"ids": ids, "english": ENGLISH},
    ).mappings()
    for r in synonyms:
        if len(context[r["id"]]["synonyms"]) < 3:
            context[r["id"]]["synonyms"].append(r["name"])
    return context


def agent_details(session: Session, concept_id: int) -> dict | None:
    """get_details trimmed for the mapping agent: what the concept is and where it sits."""
    details = get_details(session, concept_id)
    if details is None:
        return None
    attributes = [r for r in details["related"] if _describes(r["relationship_id"])]
    return {
        "concept": details["concept"],
        "synonyms": details["synonyms"][:10],
        "parents": details["parents"][:10],
        "children": details["children"][:15],
        "child_count": details["child_count"],
        "attributes": attributes[:25],
    }


def list_domains(session: Session) -> list[str]:
    return list(
        session.execute(text("SELECT domain_id FROM domain_label ORDER BY domain_id")).scalars()
    )


@cache.cached("embeddings")
def concept_facets(session: Session, embedding_table: str | None) -> list[dict]:
    """The domain / vocabulary / concept class combinations of the concepts search can find:
    valid standard concepts that are embedded (all of them when nothing is embedded yet). A few
    dozen rows, so the browser can narrow each filter by the others, as Athena does. Cached
    until embeddings change or the vocabulary is imported again."""
    source = "concept c"
    if embedding_table:
        source = f"{_ident(embedding_table)} e JOIN concept c ON c.concept_id = e.concept_id"
    rows = session.execute(
        text(
            f"""
            SELECT DISTINCT c.domain_id, c.vocabulary_id, c.concept_class_id
            FROM {source}
            WHERE c.standard_concept = 'S' AND c.invalid_reason IS NULL
              AND {EXCLUDED_CLASS_FILTER.replace("%%", "%")}
            ORDER BY 1, 2, 3
            """
        )
    ).mappings()
    return [dict(r) for r in rows]
