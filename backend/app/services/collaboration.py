"""Who is working on which source term (presence, kept briefly in Redis) and team activity."""

import csv
import io
from collections.abc import Iterator
from datetime import date

from sqlalchemy import text
from sqlmodel import Session

from app.core.db import session_scope
from app.core.redis import get_redis

# A browser renews its presence every ~20 s; without renewal it disappears after this many seconds
PRESENCE_TTL = 45


def _key(vocabulary_id: int, source_id: int, user_id: int) -> str:
    return f"presence:{vocabulary_id}:{source_id}:{user_id}"


def touch(vocabulary_id: int, source_id: int, user_id: int, username: str) -> None:
    """Mark that the user has this term open (one term per user and vocabulary)."""
    r = get_redis()
    for old in r.scan_iter(f"presence:{vocabulary_id}:*:{user_id}", count=500):
        if old != _key(vocabulary_id, source_id, user_id):
            r.delete(old)
    r.set(_key(vocabulary_id, source_id, user_id), username, ex=PRESENCE_TTL)


def leave(vocabulary_id: int, user_id: int) -> None:
    r = get_redis()
    for old in r.scan_iter(f"presence:{vocabulary_id}:*:{user_id}", count=500):
        r.delete(old)


def who(vocabulary_id: int) -> list[dict]:
    """Everyone with a term of this vocabulary open."""
    r = get_redis()
    out = []
    for key in r.scan_iter(f"presence:{vocabulary_id}:*", count=500):
        _, _, source_id, user_id = key.split(":")
        username = r.get(key)
        if username:
            out.append({"source_id": int(source_id), "user_id": int(user_id), "username": username})
    return sorted(out, key=lambda p: p["username"])


def activity(session: Session, vocabulary_id: int) -> list[dict]:
    """Per person: terms mapped by hand, suggestions decided, open flags (today and in total)."""
    rows = session.execute(
        text(
            """
            WITH mapped AS (
                SELECT a.created_by AS user_id,
                       COUNT(DISTINCT a.source_id) FILTER (WHERE a.created_at >= CURRENT_DATE) AS today,
                       COUNT(DISTINCT a.source_id) AS total
                FROM auto_mapping_audit a JOIN source_concepts sc ON sc.source_id = a.source_id
                WHERE sc.source_vocabulary_id = :v AND a.created_by IS NOT NULL
                  AND a.mapping_method = 'manual'
                GROUP BY a.created_by
            ), reviewed AS (
                SELECT ms.decided_by AS user_id,
                       COUNT(*) FILTER (WHERE ms.decided_at >= CURRENT_DATE) AS today,
                       COUNT(*) AS total
                FROM mapping_suggestion ms JOIN source_concepts sc ON sc.source_id = ms.source_id
                WHERE sc.source_vocabulary_id = :v AND ms.decided_by IS NOT NULL
                GROUP BY ms.decided_by
            ), flagged AS (
                SELECT sc.flagged_by AS user_id, COUNT(*) AS open
                FROM source_concepts sc
                WHERE sc.source_vocabulary_id = :v AND sc.flag IS NOT NULL
                  AND NOT EXISTS (SELECT 1 FROM source_standard_map m WHERE m.source_id = sc.source_id)
                GROUP BY sc.flagged_by
            )
            SELECT u.id AS user_id, u.username,
                   COALESCE(m.today, 0) AS mapped_today, COALESCE(m.total, 0) AS mapped_total,
                   COALESCE(r.today, 0) AS reviewed_today, COALESCE(r.total, 0) AS reviewed_total,
                   COALESCE(f.open, 0) AS flags_open
            FROM app_user u
            LEFT JOIN mapped m ON m.user_id = u.id
            LEFT JOIN reviewed r ON r.user_id = u.id
            LEFT JOIN flagged f ON f.user_id = u.id
            WHERE m.user_id IS NOT NULL OR r.user_id IS NOT NULL OR f.user_id IS NOT NULL
            ORDER BY COALESCE(m.today, 0) + COALESCE(r.today, 0) DESC, u.username
            """
        ),
        {"v": vocabulary_id},
    ).mappings()
    return [dict(r) for r in rows]


STCM_COLUMNS = [
    "source_code",
    "source_concept_id",
    "source_vocabulary_id",
    "source_code_description",
    "target_concept_id",
    "target_vocabulary_id",
    "valid_start_date",
    "valid_end_date",
    "invalid_reason",
]


def iter_source_to_concept_map(vocabulary_id: int, source_vocabulary_id: str) -> Iterator[str]:
    """The mappings as OMOP SOURCE_TO_CONCEPT_MAP rows (CSV), streamed."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")

    def flush() -> str:
        value = buffer.getvalue()
        buffer.seek(0)
        buffer.truncate(0)
        return value

    writer.writerow(STCM_COLUMNS)
    yield flush()
    with session_scope() as session:
        result = session.execute(
            text(
                """
                SELECT sc.source_value, sc.source_concept_name, c.concept_id, c.vocabulary_id,
                       COALESCE(m.created_at::date, CURRENT_DATE) AS valid_start_date
                FROM source_standard_map m
                JOIN source_concepts sc ON sc.source_id = m.source_id
                JOIN concept c ON c.concept_id = m.concept_id
                WHERE sc.source_vocabulary_id = :v
                ORDER BY sc.source_value, c.concept_id
                """
            ).execution_options(yield_per=5000),
            {"v": vocabulary_id},
        )
        for i, (code, name, target, target_vocab, start) in enumerate(result, 1):
            writer.writerow(
                [
                    code[:50],
                    0,
                    source_vocabulary_id,
                    (name or "")[:255],
                    target,
                    target_vocab,
                    start.strftime("%Y%m%d") if isinstance(start, date) else start,
                    "20991231",
                    "",
                ]
            )
            if i % 5000 == 0:
                yield flush()
    yield flush()
