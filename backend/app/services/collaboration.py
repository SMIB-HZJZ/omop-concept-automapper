"""Who is working on which source term (presence, kept briefly in Redis) and team activity."""

from sqlalchemy import text
from sqlmodel import Session

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
