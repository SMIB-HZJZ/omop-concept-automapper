"""Review queue (I2): pending auto-map suggestions that a user accepts or rejects."""

import math
from datetime import UTC, datetime

from sqlalchemy import text
from sqlmodel import Session

from app.services import mapping

SORTS = {
    "confidence_desc": "ms.confidence DESC, sc.freq DESC NULLS LAST",
    "confidence_asc": "ms.confidence ASC, sc.freq DESC NULLS LAST",
    "freq_desc": "sc.freq DESC NULLS LAST, ms.confidence DESC",
    "newest": "ms.created_at DESC",
}


def list_suggestions(
    session: Session,
    vocabulary_id: int,
    *,
    status: str = "pending",
    min_confidence: int | None = None,
    page: int = 1,
    per_page: int = 50,
    sort: str = "confidence_desc",
) -> dict:
    where = "sc.source_vocabulary_id = :v AND ms.status = :status"
    params: dict = {"v": vocabulary_id, "status": status}
    if min_confidence is not None:
        where += " AND ms.confidence >= :minc"
        params["minc"] = min_confidence
    total = session.execute(
        text(
            f"SELECT COUNT(*) FROM mapping_suggestion ms "
            f"JOIN source_concepts sc ON sc.source_id = ms.source_id WHERE {where}"
        ),
        params,
    ).scalar_one()
    pages = max(1, math.ceil(total / per_page))
    page = min(max(page, 1), pages)
    order = SORTS.get(sort, SORTS["confidence_desc"])
    rows = session.execute(
        text(
            f"""
            SELECT ms.id, ms.source_id, sc.source_value, sc.source_concept_name, sc.freq,
                   ms.concept_id, c.concept_name, c.domain_id, c.vocabulary_id, c.concept_class_id,
                   c.concept_code, ms.confidence, ms.method, ms.pipeline, ms.rationale,
                   ms.target_domains, ms.status, ms.created_at, ms.decided_at,
                   u.username AS decided_by
            FROM mapping_suggestion ms
            JOIN source_concepts sc ON sc.source_id = ms.source_id
            JOIN concept c ON c.concept_id = ms.concept_id
            LEFT JOIN app_user u ON u.id = ms.decided_by
            WHERE {where}
            ORDER BY {order}
            LIMIT :limit OFFSET :offset
            """
        ),
        {**params, "limit": per_page, "offset": (page - 1) * per_page},
    ).mappings()
    return {
        "items": [dict(r) for r in rows],
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": pages,
    }


def _select_pending(
    session: Session, ids: list[int] | None, vocabulary_id: int | None, min_confidence: int | None
) -> list[dict]:
    if ids:
        sql = "SELECT ms.* FROM mapping_suggestion ms WHERE ms.status = 'pending' AND ms.id = ANY(:ids)"
        params: dict = {"ids": ids}
    elif vocabulary_id is not None:
        sql = (
            "SELECT ms.* FROM mapping_suggestion ms JOIN source_concepts sc ON sc.source_id = ms.source_id "
            "WHERE ms.status = 'pending' AND sc.source_vocabulary_id = :v AND ms.confidence >= :minc"
        )
        params = {"v": vocabulary_id, "minc": min_confidence or 0}
    else:
        return []
    return [dict(r) for r in session.execute(text(sql + " FOR UPDATE OF ms"), params).mappings()]


def accept(
    session: Session,
    *,
    user_id: int,
    ids: list[int] | None = None,
    vocabulary_id: int | None = None,
    min_confidence: int | None = None,
) -> int:
    rows = _select_pending(session, ids, vocabulary_id, min_confidence)
    now = datetime.now(UTC)
    for row in rows:
        mapping.map_concepts(
            session,
            row["source_id"],
            [row["concept_id"]],
            method=row["method"],
            user_id=user_id,
            confidence=row["confidence"],
            target_domains=row["target_domains"],
            pipeline=row["pipeline"],
            task_id=row["task_id"],
            rationale=row["rationale"],
            commit=False,
        )
        # map_concepts removed the pending row; keep a decided record instead
        session.execute(
            text(
                """
                INSERT INTO mapping_suggestion
                    (source_id, concept_id, confidence, method, pipeline, target_domains, rationale,
                     task_id, status, decided_by, created_at, decided_at)
                VALUES (:source_id, :concept_id, :confidence, :method, :pipeline, :target_domains,
                        :rationale, :task_id, 'accepted', :u, :created_at, :now)
                """
            ),
            {**row, "u": user_id, "now": now},
        )
    session.commit()
    _invalidate(session, [r["source_id"] for r in rows])
    return len(rows)


def reject(
    session: Session,
    *,
    user_id: int,
    ids: list[int] | None = None,
    vocabulary_id: int | None = None,
    min_confidence: int | None = None,
) -> int:
    rows = _select_pending(session, ids, vocabulary_id, min_confidence)
    if rows:
        session.execute(
            text(
                "UPDATE mapping_suggestion SET status = 'rejected', decided_by = :u, decided_at = now() "
                "WHERE id = ANY(:ids)"
            ),
            {"u": user_id, "ids": [r["id"] for r in rows]},
        )
    session.commit()
    _invalidate(session, [r["source_id"] for r in rows])
    return len(rows)


def _invalidate(session: Session, source_ids: list[int]) -> None:
    for vocabulary_id in mapping.vocabulary_of_sources(session, source_ids):
        mapping.invalidate_vocabulary(vocabulary_id)
