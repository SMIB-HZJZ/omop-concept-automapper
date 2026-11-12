"""Source vocabularies, mappings, statistics and the audit trail."""

import csv
import io
import math
from collections.abc import Iterator, Sequence
from decimal import Decimal

from sqlalchemy import text
from sqlmodel import Session

from app.core import cache
from app.core.db import session_scope
from app.models import SourceVocabulary
from app.services.atc7 import extract_atc7_codes_from_source


def vocab_ns(vocabulary_id: int) -> str:
    return f"mapping:{vocabulary_id}"


def invalidate_vocabulary(vocabulary_id: int | None = None) -> None:
    namespaces = ["vocabularies"]
    if vocabulary_id is not None:
        namespaces.append(vocab_ns(vocabulary_id))
    cache.invalidate(*namespaces)


def invalidate_all_mappings(session: Session) -> None:
    ids = session.execute(text("SELECT id FROM source_vocabulary")).scalars().all()
    invalidate_vocabulary()
    cache.invalidate(*(vocab_ns(i) for i in ids))


# --------------------------------------------------------------------------- vocabularies


@cache.cached("vocabularies")
def list_source_vocabularies(session: Session) -> list[dict]:
    rows = session.execute(
        text(
            """
            SELECT v.id, v.name, v.description, v.is_drug, v.code_vocabularies, v.created_at,
                   COUNT(sc.source_id) AS concept_count,
                   COUNT(sc.source_id) FILTER (
                       WHERE EXISTS (SELECT 1 FROM source_standard_map m WHERE m.source_id = sc.source_id)
                   ) AS mapped_count
            FROM source_vocabulary v
            LEFT JOIN source_concepts sc ON sc.source_vocabulary_id = v.id
            GROUP BY v.id
            ORDER BY v.id
            """
        )
    ).mappings()
    return [dict(r) for r in rows]


def get_or_create_vocabulary(
    session: Session, vocabulary_id: int, name: str | None = None
) -> SourceVocabulary:
    vocab = session.get(SourceVocabulary, vocabulary_id)
    if vocab is None:
        vocab = SourceVocabulary(id=vocabulary_id, name=name or f"Vocabulary {vocabulary_id}")
        session.add(vocab)
        session.commit()
        session.refresh(vocab)
        invalidate_vocabulary(vocabulary_id)
    return vocab


def delete_vocabulary(session: Session, vocabulary_id: int) -> int:
    """Delete a source vocabulary with its concepts, mappings, audit rows and suggestions."""
    deleted = session.execute(
        text("DELETE FROM source_concepts WHERE source_vocabulary_id = :v"), {"v": vocabulary_id}
    ).rowcount
    session.execute(text("DELETE FROM source_vocabulary WHERE id = :v"), {"v": vocabulary_id})
    session.commit()
    invalidate_vocabulary(vocabulary_id)
    return deleted


# --------------------------------------------------------------------------- reads


def get_unmapped(session: Session, vocabulary_id: int, page: int, per_page: int) -> dict:
    total = session.execute(
        text(
            """
            SELECT COUNT(*) FROM source_concepts sc
            WHERE sc.source_vocabulary_id = :v
              AND NOT EXISTS (SELECT 1 FROM source_standard_map m WHERE m.source_id = sc.source_id)
            """
        ),
        {"v": vocabulary_id},
    ).scalar_one()
    pages = max(1, math.ceil(total / per_page))
    page = min(max(page, 1), pages)
    rows = session.execute(
        text(
            """
            SELECT sc.source_id, sc.source_value, sc.source_concept_name,
                   sc.source_vocabulary_id, sc.freq, 0 AS mapped_concepts,
                   ms.concept_id AS suggestion_concept_id, ms.confidence AS suggestion_confidence
            FROM source_concepts sc
            LEFT JOIN mapping_suggestion ms ON ms.source_id = sc.source_id AND ms.status = 'pending'
            WHERE sc.source_vocabulary_id = :v
              AND NOT EXISTS (SELECT 1 FROM source_standard_map m WHERE m.source_id = sc.source_id)
            ORDER BY sc.freq DESC NULLS LAST, sc.source_id
            LIMIT :limit OFFSET :offset
            """
        ),
        {"v": vocabulary_id, "limit": per_page, "offset": (page - 1) * per_page},
    ).mappings()
    items = []
    for r in rows:
        item = dict(r)
        item["atc7_codes"] = extract_atc7_codes_from_source(item["source_value"])
        items.append(item)
    return {"items": items, "total": total, "page": page, "per_page": per_page, "pages": pages}


def get_unmapped_concepts_for_automap(
    session: Session, vocabulary_id: int, limit: int | None = None
) -> list[dict]:
    rows = session.execute(
        text(
            """
            SELECT sc.source_id, sc.source_value, sc.source_concept_name, sc.freq, sc.drug_structure
            FROM source_concepts sc
            WHERE sc.source_vocabulary_id = :v
              AND NOT EXISTS (SELECT 1 FROM source_standard_map m WHERE m.source_id = sc.source_id)
              AND NOT EXISTS (
                  SELECT 1 FROM mapping_suggestion ms
                  WHERE ms.source_id = sc.source_id AND ms.status = 'pending'
              )
            ORDER BY sc.freq DESC NULLS LAST, sc.source_id
            LIMIT :limit
            """
        ),
        {"v": vocabulary_id, "limit": limit},
    ).mappings()
    return [dict(r) for r in rows]


def get_source_concept(session: Session, source_id: int) -> dict | None:
    row = (
        session.execute(
            text(
                """
            SELECT sc.source_id, sc.source_value, sc.source_concept_name,
                   sc.source_vocabulary_id, sc.freq, v.is_drug, sc.drug_structure,
                   v.code_vocabularies
            FROM source_concepts sc JOIN source_vocabulary v ON v.id = sc.source_vocabulary_id
            WHERE sc.source_id = :s
            """
            ),
            {"s": source_id},
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


MAPPED_COLUMNS = [
    "source_id",
    "source_value",
    "source_concept_name",
    "concept_name",
    "concept_id",
    "domain_id",
    "freq",
]


_MAPPED_FROM = """
    FROM source_concepts sc
    JOIN source_standard_map ssm ON sc.source_id = ssm.source_id
    JOIN concept c ON ssm.concept_id = c.concept_id
    LEFT JOIN app_user u ON u.id = ssm.created_by
    WHERE sc.source_vocabulary_id = :v
      AND (CAST(:q AS text) IS NULL
           OR sc.source_value ILIKE :q OR sc.source_concept_name ILIKE :q
           OR c.concept_name ILIKE :q OR CAST(c.concept_id AS text) = :q_exact)
"""
_MAPPED_SELECT = """
    SELECT sc.source_id, sc.source_value, sc.source_concept_name,
           c.concept_name, c.concept_id, c.domain_id, c.vocabulary_id, sc.freq,
           u.username AS mapped_by, ssm.created_at AS mapped_at
"""
_MAPPED_ORDER = " ORDER BY sc.freq DESC NULLS LAST, sc.source_id, c.concept_id"


def _search_params(q: str | None) -> dict:
    q = (q or "").strip()
    if not q:
        return {"q": None, "q_exact": None}
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return {"q": f"%{escaped}%", "q_exact": q}


def get_mapped_page(
    session: Session, vocabulary_id: int, page: int, per_page: int, q: str | None = None
) -> dict:
    """One page of mapped rows (one row per source → standard concept pair)."""
    params = {"v": vocabulary_id, **_search_params(q)}
    total = session.execute(text("SELECT COUNT(*)" + _MAPPED_FROM), params).scalar_one()
    pages = max(1, math.ceil(total / per_page))
    page = min(max(page, 1), pages)
    rows = session.execute(
        text(_MAPPED_SELECT + _MAPPED_FROM + _MAPPED_ORDER + " LIMIT :limit OFFSET :offset"),
        {**params, "limit": per_page, "offset": (page - 1) * per_page},
    ).mappings()
    return {
        "items": [dict(r) for r in rows],
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": pages,
    }


def iter_mapped_csv(vocabulary_id: int) -> Iterator[str]:
    """CSV export with the same columns as the Streamlit app's download, streamed row by row.

    Uses its own session: the request's session may already be closed while the body streams."""
    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer, fieldnames=MAPPED_COLUMNS, extrasaction="ignore", lineterminator="\n"
    )

    def flush() -> str:
        value = buffer.getvalue()
        buffer.seek(0)
        buffer.truncate(0)
        return value

    writer.writeheader()
    yield flush()
    with session_scope() as session:
        result = session.execute(
            text(_MAPPED_SELECT + _MAPPED_FROM + _MAPPED_ORDER).execution_options(yield_per=5000),
            {"v": vocabulary_id, **_search_params(None)},
        ).mappings()
        for i, row in enumerate(result, 1):
            writer.writerow(row)
            if i % 5000 == 0:
                yield flush()
    yield flush()


@cache.cached(lambda session, vocabulary_id: vocab_ns(vocabulary_id))
def get_stats(session: Session, vocabulary_id: int) -> dict:
    counts = session.execute(
        text(
            """
            SELECT COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE EXISTS (
                       SELECT 1 FROM source_standard_map m WHERE m.source_id = sc.source_id)) AS mapped,
                   COALESCE(SUM(sc.freq) FILTER (WHERE EXISTS (
                       SELECT 1 FROM source_standard_map m WHERE m.source_id = sc.source_id)), 0) AS mapped_freq,
                   COALESCE(SUM(sc.freq), 0) AS total_freq
            FROM source_concepts sc
            WHERE sc.source_vocabulary_id = :v
            """
        ),
        {"v": vocabulary_id},
    ).one()
    mapping_counts = session.execute(
        text(
            """
            SELECT COUNT(*) AS total_mappings, COUNT(DISTINCT ssm.concept_id) AS unique_concepts,
                   COALESCE(SUM(sc.freq), 0) AS mapping_freq
            FROM source_standard_map ssm JOIN source_concepts sc ON sc.source_id = ssm.source_id
            WHERE sc.source_vocabulary_id = :v
            """
        ),
        {"v": vocabulary_id},
    ).one()
    by_domain = session.execute(
        text(
            """
            SELECT c.domain_id, COUNT(*) AS count
            FROM source_standard_map ssm
            JOIN source_concepts sc ON sc.source_id = ssm.source_id
            JOIN concept c ON c.concept_id = ssm.concept_id
            WHERE sc.source_vocabulary_id = :v
            GROUP BY c.domain_id ORDER BY count DESC
            """
        ),
        {"v": vocabulary_id},
    ).mappings()
    pending = session.execute(
        text(
            """
            SELECT COUNT(*) FROM mapping_suggestion ms JOIN source_concepts sc ON sc.source_id = ms.source_id
            WHERE sc.source_vocabulary_id = :v AND ms.status = 'pending'
            """
        ),
        {"v": vocabulary_id},
    ).scalar_one()
    total, mapped = int(counts.total), int(counts.mapped)
    return {
        "total": total,
        "mapped": mapped,
        "unmapped": total - mapped,
        "percentage": (mapped / total * 100) if total else 0.0,
        "total_mappings": int(mapping_counts.total_mappings),
        "unique_concepts": int(mapping_counts.unique_concepts),
        # Same definition as the Streamlit app: sum of freq over all mapping rows
        "frequency_coverage": int(mapping_counts.mapping_freq),
        "mapped_frequency": int(counts.mapped_freq),
        "total_frequency": int(counts.total_freq),
        "pending_suggestions": int(pending),
        "by_domain": [dict(r) for r in by_domain],
    }


@cache.cached(lambda session, vocabulary_id: vocab_ns(vocabulary_id))
def get_audit_stats(session: Session, vocabulary_id: int) -> list[dict]:
    rows = session.execute(
        text(
            """
            SELECT mapping_method,
                   COUNT(*) AS mapping_count,
                   AVG(confidence_score) AS avg_confidence,
                   MIN(confidence_score) AS min_confidence,
                   MAX(confidence_score) AS max_confidence
            FROM auto_mapping_audit ama
            JOIN source_concepts sc ON ama.source_id = sc.source_id
            WHERE sc.source_vocabulary_id = :v
            GROUP BY mapping_method
            ORDER BY mapping_count DESC
            """
        ),
        {"v": vocabulary_id},
    ).mappings()
    return [_floats(dict(r)) for r in rows]


def get_audit_recent(session: Session, vocabulary_id: int, limit: int = 10) -> list[dict]:
    rows = session.execute(
        text(
            """
            SELECT ama.audit_id, sc.source_id, sc.source_concept_name,
                   c.concept_id, c.concept_name AS mapped_concept_name,
                   ama.confidence_score, ama.mapping_method, ama.target_domains,
                   ama.pipeline, ama.rationale, u.username AS created_by, ama.created_at
            FROM auto_mapping_audit ama
            JOIN source_concepts sc ON ama.source_id = sc.source_id
            JOIN concept c ON ama.concept_id = c.concept_id
            LEFT JOIN app_user u ON u.id = ama.created_by
            WHERE sc.source_vocabulary_id = :v
            ORDER BY ama.created_at DESC, ama.audit_id DESC
            LIMIT :limit
            """
        ),
        {"v": vocabulary_id, "limit": limit},
    ).mappings()
    return [_floats(dict(r)) for r in rows]


def _floats(row: dict) -> dict:
    return {k: float(v) if isinstance(v, Decimal) else v for k, v in row.items()}


# --------------------------------------------------------------------------- writes


def vocabulary_of_sources(session: Session, source_ids: Sequence[int]) -> set[int]:
    if not source_ids:
        return set()
    rows = session.execute(
        text(
            "SELECT DISTINCT source_vocabulary_id FROM source_concepts WHERE source_id = ANY(:ids)"
        ),
        {"ids": list(source_ids)},
    ).scalars()
    return set(rows)


def existing_concept_ids(session: Session, concept_ids: Sequence[int]) -> set[int]:
    if not concept_ids:
        return set()
    rows = session.execute(
        text("SELECT concept_id FROM concept WHERE concept_id = ANY(:ids)"),
        {"ids": list(concept_ids)},
    ).scalars()
    return set(rows)


def map_concepts(
    session: Session,
    source_id: int,
    concept_ids: Sequence[int],
    *,
    method: str = "manual",
    user_id: int | None = None,
    confidence: float | None = None,
    target_domains: list[str] | None = None,
    pipeline: str | None = None,
    task_id: str | None = None,
    rationale: str | None = None,
    commit: bool = True,
    only_if_unmapped: bool = False,
    match_type: str | None = None,
) -> bool:
    """Replace the mappings of one source concept and write one audit row per concept.

    With ``only_if_unmapped`` (auto-map in commit mode) nothing is written when the concept was
    mapped in the meantime, e.g. by hand while a long run was going. Returns whether it wrote.
    """
    concept_ids = list(dict.fromkeys(concept_ids))
    # Serialise writers of the same source concept (manual mapping vs. a running auto-map)
    session.execute(
        text("SELECT 1 FROM source_concepts WHERE source_id = :s FOR UPDATE"), {"s": source_id}
    )
    if (
        only_if_unmapped
        and session.execute(
            text("SELECT EXISTS (SELECT 1 FROM source_standard_map WHERE source_id = :s)"),
            {"s": source_id},
        ).scalar_one()
    ):
        return False
    session.execute(text("DELETE FROM source_standard_map WHERE source_id = :s"), {"s": source_id})
    # A manual/accepted mapping supersedes a pending suggestion
    session.execute(
        text("DELETE FROM mapping_suggestion WHERE source_id = :s AND status = 'pending'"),
        {"s": source_id},
    )
    for concept_id in concept_ids:
        params = {"s": source_id, "c": concept_id, "u": user_id}
        session.execute(
            text(
                "INSERT INTO source_standard_map (source_id, concept_id, created_by) "
                "VALUES (:s, :c, :u) ON CONFLICT DO NOTHING"
            ),
            params,
        )
        session.execute(
            text(
                """
                INSERT INTO auto_mapping_audit
                    (source_id, concept_id, confidence_score, mapping_method, target_domains,
                     pipeline, task_id, rationale, created_by, match_type)
                VALUES (:s, :c, :conf, :method, :domains, :pipeline, :task, :rationale, :u,
                        :match_type)
                """
            ),
            {
                **params,
                "conf": confidence,
                "method": method,
                "domains": target_domains or [],
                "pipeline": pipeline,
                "task": task_id,
                "rationale": rationale,
                "match_type": match_type,
            },
        )
    if commit:
        session.commit()
    return True


def unmap(session: Session, source_ids: Sequence[int]) -> int:
    if not source_ids:
        return 0
    deleted = session.execute(
        text("DELETE FROM source_standard_map WHERE source_id = ANY(:ids)"),
        {"ids": list(source_ids)},
    ).rowcount
    session.commit()
    return deleted


def clean_non_standard_mappings(conn) -> int:
    """Remove mappings to concepts that are no longer standard (raw psycopg connection)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            DELETE FROM source_standard_map
            WHERE concept_id IN (
                SELECT concept_id FROM concept
                WHERE standard_concept IS NULL OR standard_concept != 'S'
            )
            """
        )
        return cur.rowcount
