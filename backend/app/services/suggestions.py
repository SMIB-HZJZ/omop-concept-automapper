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
    # Most patient records affected by the least certain decisions first
    "priority": "COALESCE(sc.freq, 1) * (11 - ms.confidence) DESC, sc.freq DESC NULLS LAST",
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
    source_id: int | None = None,
) -> dict:
    where = "sc.source_vocabulary_id = :v AND ms.status = :status"
    params: dict = {"v": vocabulary_id, "status": status}
    if source_id is not None:
        where += " AND ms.source_id = :sid"
        params["sid"] = source_id
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
                   c.concept_code, ms.confidence, ms.method, ms.pipeline, ms.rationale, ms.match_type,
                   ms.decision_path,
                   ms.target_domains, ms.status, ms.created_at, ms.decided_at,
                   u.username AS decided_by,
                   COALESCE((
                       SELECT json_agg(json_build_object(
                           'concept_id', x.concept_id, 'concept_name', x.concept_name,
                           'concept_class_id', x.concept_class_id) ORDER BY x.concept_id)
                       FROM concept x WHERE x.concept_id = ANY(ms.extra_concept_ids)
                   ), '[]') AS extra_concepts
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
    # The vocabulary lock comes before the suggestion rows (see mapping.lock_vocabularies)
    if vocabulary_id is not None:
        mapping.lock_vocabularies(session, [vocabulary_id])
    elif ids:
        mapping.lock_vocabularies(
            session,
            session.execute(
                text(
                    "SELECT DISTINCT sc.source_vocabulary_id FROM mapping_suggestion ms "
                    "JOIN source_concepts sc ON sc.source_id = ms.source_id WHERE ms.id = ANY(:ids)"
                ),
                {"ids": ids},
            )
            .scalars()
            .all(),
        )
    rows = _select_pending(session, ids, vocabulary_id, min_confidence)
    now = datetime.now(UTC)
    for row in rows:
        mapping.map_concepts(
            session,
            row["source_id"],
            [row["concept_id"], *(row.get("extra_concept_ids") or [])],
            method=row["method"],
            user_id=user_id,
            confidence=row["confidence"],
            target_domains=row["target_domains"],
            pipeline=row["pipeline"],
            task_id=row["task_id"],
            rationale=row["rationale"],
            match_type=row.get("match_type"),
            commit=False,
        )
        # map_concepts removed the pending row; keep a decided record instead
        session.execute(
            text(
                """
                INSERT INTO mapping_suggestion
                    (source_id, concept_id, extra_concept_ids, confidence, method, pipeline,
                     target_domains, rationale, task_id, match_type, decision_path, status,
                     decided_by, created_at, decided_at)
                VALUES (:source_id, :concept_id, :extra_concept_ids, :confidence, :method, :pipeline,
                        :target_domains, :rationale, :task_id, :match_type, :decision_path,
                        'accepted', :u, :created_at, :now)
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


def calibration(
    session: Session, vocabulary_id: int | None, target_precision: float = 0.95
) -> dict:
    """Precision of auto-map suggestions per confidence level, from review decisions.

    A suggestion counts as correct when it was accepted. The recommended threshold is the lowest
    confidence from which on the accepted share is at least ``target_precision``."""
    where = "ms.status IN ('accepted', 'rejected')"
    params: dict = {}
    if vocabulary_id is not None:
        where += " AND sc.source_vocabulary_id = :v"
        params["v"] = vocabulary_id
    rows = session.execute(
        text(
            f"""
            SELECT ms.confidence, ms.method,
                   COUNT(*) FILTER (WHERE ms.status = 'accepted') AS accepted,
                   COUNT(*) FILTER (WHERE ms.status = 'rejected') AS rejected
            FROM mapping_suggestion ms JOIN source_concepts sc ON sc.source_id = ms.source_id
            WHERE {where}
            GROUP BY ms.confidence, ms.method
            ORDER BY ms.confidence DESC, ms.method
            """
        ),
        params,
    ).mappings()
    levels: dict[int, dict] = {}
    methods: dict[str, dict] = {}
    for r in rows:
        level = levels.setdefault(
            r["confidence"], {"confidence": r["confidence"], "accepted": 0, "rejected": 0}
        )
        level["accepted"] += r["accepted"]
        level["rejected"] += r["rejected"]
        method = methods.setdefault(
            r["method"], {"method": r["method"], "accepted": 0, "rejected": 0}
        )
        method["accepted"] += r["accepted"]
        method["rejected"] += r["rejected"]
    recommended = None
    accepted = decided = 0
    for confidence in sorted(levels, reverse=True):
        accepted += levels[confidence]["accepted"]
        decided += levels[confidence]["accepted"] + levels[confidence]["rejected"]
        levels[confidence]["precision_at_or_above"] = accepted / decided
        if accepted / decided >= target_precision:
            recommended = confidence
    for m in methods.values():
        total = m["accepted"] + m["rejected"]
        m["precision"] = m["accepted"] / total if total else None
    return {
        "decided": sum(lv["accepted"] + lv["rejected"] for lv in levels.values()),
        "target_precision": target_precision,
        "recommended_threshold": recommended,
        "levels": sorted(levels.values(), key=lambda lv: -lv["confidence"]),
        "methods": sorted(methods.values(), key=lambda m: m["method"]),
    }
