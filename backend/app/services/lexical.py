"""Text-based mapping aids that need no LLM: source codes, exact names and synonyms, keyword
(trigram) search, earlier reviewed mappings and similar mapped examples.

Every function opens its own short session, so the workflow steps can call them from threads.
Non-standard hits are followed through ``Maps to`` to their standard concepts.
"""

import re
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlmodel import Session

from app.core.db import get_engine
from app.vector.store import CONCEPT_FIELDS, EXCLUDED_CLASS_FILTER, SearchFilters

# A mapping counts as reviewed unless an auto-map run wrote it and nobody accepted it in review
REVIEWED_MAPPING = """
    NOT EXISTS (
        SELECT 1 FROM auto_mapping_audit a
        WHERE a.source_id = m.source_id AND a.concept_id = m.concept_id
          AND a.mapping_method LIKE 'auto%'
          AND NOT EXISTS (
              SELECT 1 FROM mapping_suggestion s
              WHERE s.source_id = m.source_id AND s.concept_id = m.concept_id
                AND s.status = 'accepted'))
"""


@dataclass
class TextMatch:
    """Standard concepts found for a source term. ``ambiguous``: several unrelated targets."""

    concepts: list[dict] = field(default_factory=list)
    via: str = ""
    ambiguous: bool = False


def _concepts(session: Session, ids: list[int]) -> list[dict]:
    if not ids:
        return []
    rows = session.execute(
        text(f"SELECT {CONCEPT_FIELDS} FROM concept c WHERE c.concept_id = ANY(:ids)"),
        {"ids": ids},
    ).mappings()
    by_id = {r["concept_id"]: dict(r) for r in rows}
    return [by_id[i] for i in ids if i in by_id]


def standard_targets(session: Session, concept_ids: list[int]) -> dict[int, list[int]]:
    """Each concept's standard target(s): itself if standard, else its valid ``Maps to``."""
    rows = session.execute(
        text(
            """
            SELECT c.concept_id AS source,
                   CASE WHEN c.standard_concept = 'S' THEN c.concept_id ELSE r.concept_id_2 END AS target
            FROM concept c
            LEFT JOIN concept_relationship r
              ON r.concept_id_1 = c.concept_id AND r.relationship_id = 'Maps to'
             AND r.invalid_reason IS NULL AND c.standard_concept IS DISTINCT FROM 'S'
            LEFT JOIN concept t ON t.concept_id = r.concept_id_2
            WHERE c.concept_id = ANY(:ids)
              AND (c.standard_concept = 'S' OR (t.standard_concept = 'S' AND t.invalid_reason IS NULL))
            ORDER BY 1, 2
            """
        ),
        {"ids": concept_ids},
    ).all()
    targets: dict[int, list[int]] = {}
    for source, target in rows:
        targets.setdefault(source, [])
        if target not in targets[source]:
            targets[source].append(target)
    return targets


def _resolve(session: Session, hit_ids: list[int], via: str) -> TextMatch:
    """Hits -> standard targets. One target set for all hits = a match (several targets of one
    source concept, e.g. an ICD-10 code mapping to two SNOMED concepts, are mapped together)."""
    targets = standard_targets(session, hit_ids)
    sets = {tuple(t) for t in targets.values() if t}
    if not sets:
        return TextMatch(via=via)
    if len(sets) > 1:
        ids = sorted({i for s in sets for i in s})
        return TextMatch(_concepts(session, ids), via, ambiguous=True)
    return TextMatch(_concepts(session, list(next(iter(sets)))), via)


# --------------------------------------------------------------------------- source codes


def code_candidates(source_value: str) -> list[str]:
    """Spellings of a code to look up: as is, its leading token, ICD-style with/without dot."""
    value = (source_value or "").strip().upper()
    if not value:
        return []
    found = [value]
    token = re.match(r"[A-Z0-9][A-Z0-9.]*", value)
    if token:
        found.append(token.group(0).rstrip("."))
    for code in list(found):
        if re.fullmatch(r"[A-Z]\d{2}\d{1,2}", code):  # E119 -> E11.9
            found.append(f"{code[:3]}.{code[3:]}")
        if re.fullmatch(r"[A-Z]\d{2}\.\d{1,2}", code):  # E11.9 -> E119
            found.append(code.replace(".", ""))
    return list(dict.fromkeys(found))


def match_code(source_value: str, vocabularies: list[str]) -> TextMatch:
    candidates = code_candidates(source_value)
    if not candidates or not vocabularies:
        return TextMatch()
    with Session(get_engine()) as session:
        ids = list(
            session.execute(
                text(
                    """
                    SELECT concept_id FROM concept
                    WHERE upper(concept_code) = ANY(:codes) AND vocabulary_id = ANY(:vocabs)
                      AND invalid_reason IS NULL
                    ORDER BY concept_id
                    """
                ),
                {"codes": candidates, "vocabs": vocabularies},
            ).scalars()
        )
        return _resolve(session, ids, "code") if ids else TextMatch()


# --------------------------------------------------------------------------- exact names


def match_name(names: list[str], vocabularies: list[str] | None = None) -> TextMatch:
    """Concepts whose name or synonym equals one of ``names`` (case, accents and punctuation
    ignored), followed to their standard concepts."""
    names = [n for n in dict.fromkeys(names) if n and n.strip()]
    if not names:
        return TextMatch()
    with Session(get_engine()) as session:
        ids = list(
            session.execute(
                text(
                    """
                    WITH wanted AS (SELECT norm_text(n) AS n FROM unnest(CAST(:names AS text[])) n)
                    SELECT DISTINCT c.concept_id FROM concept c
                    WHERE norm_text(c.concept_name) IN (SELECT n FROM wanted)
                      AND c.invalid_reason IS NULL
                      AND (CAST(:vocabs AS text[]) IS NULL OR c.vocabulary_id = ANY(:vocabs))
                    UNION
                    SELECT DISTINCT s.concept_id FROM concept_synonym s
                    JOIN concept c ON c.concept_id = s.concept_id AND c.invalid_reason IS NULL
                    WHERE norm_text(s.concept_synonym_name) IN (SELECT n FROM wanted)
                      AND (CAST(:vocabs AS text[]) IS NULL OR c.vocabulary_id = ANY(:vocabs))
                    """
                ),
                {"names": names, "vocabs": vocabularies},
            ).scalars()
        )
        return _resolve(session, sorted(ids), "name") if ids else TextMatch()


# --------------------------------------------------------------------------- keyword search


def keyword_search(
    query: str, k: int, filters: SearchFilters, min_similarity: float = 0.3
) -> list[dict]:
    """Trigram search over standard concept names and their synonyms (best score per concept)."""
    query = (query or "").strip().lower()
    if len(query) < 3:
        return []
    where = [
        "c.standard_concept = 'S'",
        "c.invalid_reason IS NULL",
        EXCLUDED_CLASS_FILTER.replace("%%", "%"),
    ]
    params: dict = {"q": query, "k": k, "limit": min_similarity}
    if filters.domains:
        where.append("c.domain_id = ANY(:domains)")
        params["domains"] = list(filters.domains)
    where += filters.where(params)
    if filters.atc7_codes:
        where.append(
            "EXISTS (SELECT 1 FROM concept_atc7 a WHERE a.concept_id = c.concept_id "
            "AND a.atc7_codes && CAST(:atc AS text[]))"
        )
        params["atc"] = list(filters.atc7_codes)
    condition = " AND ".join(where)
    with Session(get_engine()) as session:
        session.execute(
            text("SELECT set_config('pg_trgm.word_similarity_threshold', :t, true)"),
            {"t": str(min_similarity)},
        )
        rows = session.execute(
            text(
                f"""
                WITH hits AS (
                    SELECT c.concept_id, word_similarity(:q, lower(c.concept_name)) AS score
                    FROM concept c
                    WHERE :q <% lower(c.concept_name) AND {condition}
                    UNION ALL
                    SELECT c.concept_id, word_similarity(:q, lower(s.concept_synonym_name))
                    FROM concept_synonym s JOIN concept c ON c.concept_id = s.concept_id
                    WHERE :q <% lower(s.concept_synonym_name) AND {condition}
                ), best AS (
                    SELECT concept_id, MAX(score) AS score FROM hits GROUP BY concept_id
                    ORDER BY score DESC LIMIT :k
                )
                SELECT {CONCEPT_FIELDS}, ca.atc7_codes, best.score
                FROM best JOIN concept c ON c.concept_id = best.concept_id
                LEFT JOIN concept_atc7 ca ON ca.concept_id = c.concept_id
                ORDER BY best.score DESC, c.concept_id
                """
            ),
            params,
        ).mappings()
        results = [dict(r) for r in rows]
        session.rollback()
    return [{**r, "score": float(r["score"]), "rank": i + 1} for i, r in enumerate(results)]


# --------------------------------------------------------------------------- earlier mappings


def match_previous(source_name: str, exclude_source_id: int | None) -> TextMatch:
    """Reviewed mappings of other source concepts with the same (normalised) name."""
    if not source_name or not source_name.strip():
        return TextMatch()
    with Session(get_engine()) as session:
        rows = session.execute(
            text(
                f"""
                SELECT sc.source_id, array_agg(m.concept_id ORDER BY m.concept_id) AS concepts
                FROM source_concepts sc
                JOIN source_standard_map m ON m.source_id = sc.source_id
                JOIN concept c ON c.concept_id = m.concept_id
                WHERE norm_text(sc.source_concept_name) = norm_text(:name)
                  AND sc.source_id IS DISTINCT FROM :exclude
                  AND c.standard_concept = 'S' AND c.invalid_reason IS NULL
                  AND {REVIEWED_MAPPING}
                GROUP BY sc.source_id
                """
            ),
            {"name": source_name, "exclude": exclude_source_id},
        ).all()
        sets = {tuple(concepts) for _, concepts in rows}
        if not sets:
            return TextMatch()
        if len(sets) > 1:
            ids = sorted({i for s in sets for i in s})
            return TextMatch(_concepts(session, ids), "previous", ambiguous=True)
        return TextMatch(_concepts(session, list(next(iter(sets)))), "previous")


def similar_examples(source_name: str, exclude_source_id: int | None, limit: int = 5) -> list[dict]:
    """Reviewed mappings of similar source names: shown to the reranker as worked examples."""
    if not source_name or len(source_name.strip()) < 3 or limit <= 0:
        return []
    with Session(get_engine()) as session:
        session.execute(text("SELECT set_config('pg_trgm.similarity_threshold', '0.35', true)"))
        rows = session.execute(
            text(
                f"""
                SELECT sc.source_concept_name AS source_name,
                       string_agg(c.concept_name || ' [' || c.concept_class_id || ']', ' + '
                                  ORDER BY c.concept_id) AS mapped_to,
                       similarity(lower(sc.source_concept_name), lower(:name)) AS score
                FROM source_concepts sc
                JOIN source_standard_map m ON m.source_id = sc.source_id
                JOIN concept c ON c.concept_id = m.concept_id
                WHERE lower(sc.source_concept_name) % lower(:name)
                  AND sc.source_id IS DISTINCT FROM :exclude
                  AND norm_text(sc.source_concept_name) <> norm_text(:name)
                  AND {REVIEWED_MAPPING}
                GROUP BY sc.source_id, sc.source_concept_name
                ORDER BY score DESC
                LIMIT :limit
                """
            ),
            {"name": source_name, "exclude": exclude_source_id, "limit": limit},
        ).mappings()
        examples = [dict(r) for r in rows]
        session.rollback()
    return examples
