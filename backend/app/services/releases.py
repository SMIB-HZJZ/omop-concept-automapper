"""Mapping releases of a source vocabulary.

Publishing a release copies the current mappings into ``mapping_release_row`` and locks the
vocabulary: a database trigger (migration 0016) then refuses every change to its mappings and
source concepts, from any code path. An admin starts the next release to unlock it; an OMOP
vocabulary update that has to replace or remove mappings starts it automatically.

The OMOP SOURCE_TO_CONCEPT_MAP of a release is built from the copies of every release up to it:
a mapping is valid from the release it first appeared in, and one that a later release changed or
removed ends the day before that release, with ``invalid_reason`` 'U' (the source code was
remapped) or 'D' (its mapping was removed), as in the OMOP vocabulary tables.
"""

import csv
import io
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import text
from sqlmodel import Session

from app.core.db import session_scope
from app.services.mapping import invalidate_vocabulary
from app.services.tasks import ACTIVE_STATUSES

VALID_FOREVER = date(2099, 12, 31)

# Tasks that change a vocabulary's mappings or terms: a release must not be published under them
_WRITING_TASKS = ("automap.run", "source.import")

_CURRENT = """
    SELECT m.source_id, m.concept_id FROM source_standard_map m
    JOIN source_concepts sc ON sc.source_id = m.source_id
    WHERE sc.source_vocabulary_id = :v
"""
_RELEASED = "SELECT source_id, concept_id FROM mapping_release_row WHERE release_id = {param}"


# Same wording as the lock trigger's error (migration 0016)
LOCKED_MESSAGE = (
    'Source vocabulary "{name}" is released and locked. An admin can start the next release on '
    "Check & commit to change it."
)


class ReleaseError(Exception):
    """A release action that cannot be done in the vocabulary's current state (HTTP 409)."""


def _changes(session: Session, old_sql: str, new_sql: str, params: dict) -> dict[str, int]:
    """Source concepts whose mappings were added, changed or removed from ``old`` to ``new``."""
    row = session.execute(
        text(
            f"""
            WITH a AS ({old_sql}), b AS ({new_sql}),
                 sa AS (SELECT source_id, array_agg(concept_id ORDER BY concept_id) AS c
                        FROM a GROUP BY source_id),
                 sb AS (SELECT source_id, array_agg(concept_id ORDER BY concept_id) AS c
                        FROM b GROUP BY source_id)
            SELECT COUNT(*) FILTER (WHERE sa.source_id IS NULL) AS added,
                   COUNT(*) FILTER (WHERE sa.c <> sb.c) AS changed,
                   COUNT(*) FILTER (WHERE sb.source_id IS NULL) AS removed
            FROM sa FULL JOIN sb ON sa.source_id = sb.source_id
            """
        ),
        params,
    ).one()
    return {"added": row.added, "changed": row.changed, "removed": row.removed}


_NO_ROWS = "SELECT NULL::int AS source_id, NULL::int AS concept_id WHERE FALSE"


def _releases(session: Session, vocabulary_id: int) -> list[dict]:
    rows = session.execute(
        text(
            """
            SELECT r.id, r.number, r.released_at, r.note, u.username AS released_by,
                   (SELECT COUNT(DISTINCT source_id) FROM mapping_release_row x
                    WHERE x.release_id = r.id) AS mapped_sources,
                   (SELECT COUNT(*) FROM mapping_release_row x WHERE x.release_id = r.id) AS rows
            FROM mapping_release r LEFT JOIN app_user u ON u.id = r.released_by
            WHERE r.vocabulary_id = :v ORDER BY r.number
            """
        ),
        {"v": vocabulary_id},
    ).mappings()
    return [dict(r) for r in rows]


def release_state(session: Session, vocabulary_id: int) -> dict:
    """Lock state, the releases (newest first, each with its changes against the one before) and,
    while unlocked, the draft's changes against the latest release."""
    vocab = session.execute(
        text("SELECT locked, draft_note FROM source_vocabulary WHERE id = :v"), {"v": vocabulary_id}
    ).one()
    releases = _releases(session, vocabulary_id)
    previous: int | None = None
    for release in releases:
        old = _RELEASED.format(param=":old") if previous else _NO_ROWS
        release.update(
            _changes(
                session,
                old,
                _RELEASED.format(param=":new"),
                {"old": previous, "new": release["id"]},
            )
        )
        previous = release["id"]
    draft = None
    if not vocab.locked:
        old = _RELEASED.format(param=":old") if previous else _NO_ROWS
        draft = _changes(session, old, _CURRENT, {"old": previous, "v": vocabulary_id})
    return {
        "locked": vocab.locked,
        "draft_note": vocab.draft_note,
        "releases": list(reversed(releases)),
        "draft": draft,
    }


def _lock_vocabulary_row(session: Session, vocabulary_id: int):
    return session.execute(
        text("SELECT locked FROM source_vocabulary WHERE id = :v FOR UPDATE"), {"v": vocabulary_id}
    ).one()


def publish(session: Session, vocabulary_id: int, user_id: int | None, note: str | None) -> int:
    """Copy the current mappings into a new release and lock the vocabulary; returns its number."""
    if _lock_vocabulary_row(session, vocabulary_id).locked:
        raise ReleaseError("This vocabulary is already released and locked")
    busy = session.execute(
        text(
            """
            SELECT EXISTS (SELECT 1 FROM task_run
                           WHERE status = ANY(:active) AND kind = ANY(:kinds)
                             AND (params->>'vocabulary_id')::int = :v)
            """
        ),
        {"active": list(ACTIVE_STATUSES), "kinds": list(_WRITING_TASKS), "v": vocabulary_id},
    ).scalar_one()
    if busy:
        raise ReleaseError("An auto-map or import of this vocabulary is running; wait for it first")
    latest = session.execute(
        text(
            "SELECT id, number FROM mapping_release WHERE vocabulary_id = :v "
            "ORDER BY number DESC LIMIT 1"
        ),
        {"v": vocabulary_id},
    ).one_or_none()
    params = {"v": vocabulary_id, "old": latest.id if latest else None}
    current = session.execute(text(f"SELECT COUNT(*) FROM ({_CURRENT}) c"), params).scalar_one()
    if not current:
        raise ReleaseError("There are no mappings to release")
    if latest:
        changes = _changes(session, _RELEASED.format(param=":old"), _CURRENT, params)
        if not any(changes.values()):
            raise ReleaseError(f"Nothing changed since release {latest.number}")
    number = (latest.number if latest else 0) + 1
    release_id = session.execute(
        text(
            """
            INSERT INTO mapping_release (vocabulary_id, number, released_by, note)
            VALUES (:v, :n, :u, :note) RETURNING id
            """
        ),
        {"v": vocabulary_id, "n": number, "u": user_id, "note": note or None},
    ).scalar_one()
    session.execute(
        text(
            """
            INSERT INTO mapping_release_row
                (release_id, source_id, source_value, source_concept_name, concept_id,
                 target_vocabulary_id, match_type)
            SELECT :r, sc.source_id, sc.source_value, sc.source_concept_name, c.concept_id,
                   c.vocabulary_id, m.match_type
            FROM source_standard_map m
            JOIN source_concepts sc ON sc.source_id = m.source_id
            JOIN concept c ON c.concept_id = m.concept_id
            WHERE sc.source_vocabulary_id = :v
            """
        ),
        {"r": release_id, "v": vocabulary_id},
    )
    session.execute(
        text("UPDATE source_vocabulary SET locked = TRUE, draft_note = NULL WHERE id = :v"),
        {"v": vocabulary_id},
    )
    session.commit()
    invalidate_vocabulary(vocabulary_id)
    return number


def start_next(session: Session, vocabulary_id: int) -> None:
    """Unlock a released vocabulary: changes from now on go into the next release."""
    if not _lock_vocabulary_row(session, vocabulary_id).locked:
        raise ReleaseError("This vocabulary is not locked: the next release is already open")
    session.execute(
        text("UPDATE source_vocabulary SET locked = FALSE, draft_note = NULL WHERE id = :v"),
        {"v": vocabulary_id},
    )
    session.commit()
    invalidate_vocabulary(vocabulary_id)


def reopen_for_vocabulary_update(cur) -> int:
    """OMOP vocabulary update (raw psycopg cursor, temp table ``stale_map`` filled): open the
    next release of every locked vocabulary whose mappings it has to replace or remove, with a
    note saying why. Returns how many were opened."""
    cur.execute(
        """
        UPDATE source_vocabulary v
        SET locked = FALSE,
            draft_note = 'Opened by the OMOP vocabulary update of ' || to_char(now(), 'YYYY-MM-DD')
                || ': mappings to concepts that are no longer standard were replaced or removed'
                || ' (Review shows them as vocabulary updates). Check them before the next release.'
        WHERE v.locked AND EXISTS (
            SELECT 1 FROM stale_map s JOIN source_concepts sc ON sc.source_id = s.source_id
            WHERE sc.source_vocabulary_id = v.id)
        """
    )
    return cur.rowcount


# --------------------------------------------------------------------------- OMOP export

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


@dataclass
class _Snapshot:
    number: int | None  # None: the unreleased draft (current mappings)
    day: date
    # (source_id, concept_id) -> (source_value, source_concept_name, target_vocabulary_id, since)
    rows: dict[tuple[int, int], tuple]


def release_exists(session: Session, vocabulary_id: int, number: int) -> bool:
    return session.execute(
        text(
            "SELECT EXISTS (SELECT 1 FROM mapping_release WHERE vocabulary_id = :v AND number = :n)"
        ),
        {"v": vocabulary_id, "n": number},
    ).scalar_one()


def _snapshots(session: Session, vocabulary_id: int, release: int | str | None) -> list[_Snapshot]:
    """Releases up to ``release`` (a number, ``None`` for the latest) and, for ``"draft"`` or a
    vocabulary without releases, the current mappings as a last, unreleased snapshot."""
    releases = session.execute(
        text(
            "SELECT id, number, released_at::date AS day FROM mapping_release "
            "WHERE vocabulary_id = :v ORDER BY number"
        ),
        {"v": vocabulary_id},
    ).all()
    if isinstance(release, int):
        if release not in {r.number for r in releases}:
            raise LookupError(f"Release {release} not found")
        releases = [r for r in releases if r.number <= release]
    snapshots = []
    for r in releases:
        rows = session.execute(
            text(
                "SELECT source_id, concept_id, source_value, source_concept_name, "
                "target_vocabulary_id FROM mapping_release_row WHERE release_id = :r"
            ),
            {"r": r.id},
        ).all()
        snapshots.append(_Snapshot(r.number, r.day, {(x[0], x[1]): (*x[2:], None) for x in rows}))
    if release == "draft" or not releases:
        rows = session.execute(
            text(
                """
                SELECT sc.source_id, c.concept_id, sc.source_value, sc.source_concept_name,
                       c.vocabulary_id, COALESCE(m.created_at::date, CURRENT_DATE)
                FROM source_standard_map m
                JOIN source_concepts sc ON sc.source_id = m.source_id
                JOIN concept c ON c.concept_id = m.concept_id
                WHERE sc.source_vocabulary_id = :v
                """
            ),
            {"v": vocabulary_id},
        ).all()
        snapshots.append(_Snapshot(None, date.today(), {(x[0], x[1]): tuple(x[2:]) for x in rows}))
    return snapshots


def stcm_rows(session: Session, vocabulary_id: int, release: int | str | None) -> list[dict]:
    """SOURCE_TO_CONCEPT_MAP rows: one per period a mapping was in consecutive snapshots."""
    snapshots = _snapshots(session, vocabulary_id, release)
    last = len(snapshots) - 1
    sources_in = [{key[0] for key in s.rows} for s in snapshots]
    present: dict[tuple[int, int], list[int]] = {}
    for i, snapshot in enumerate(snapshots):
        for key in snapshot.rows:
            present.setdefault(key, []).append(i)
    out = []
    for key, indexes in present.items():
        runs: list[list[int]] = []
        for i in indexes:
            if runs and runs[-1][-1] == i - 1:
                runs[-1].append(i)
            else:
                runs.append([i])
        for run in runs:
            first, end_index = run[0], run[-1]
            value, name, target_vocabulary, since = snapshots[end_index].rows[key]
            # A draft mapping is not released yet: valid from when it was made
            start = since if snapshots[first].number is None and since else snapshots[first].day
            if end_index == last:
                end, reason = VALID_FOREVER, ""
            else:
                following = end_index + 1
                end = max(start, snapshots[following].day - timedelta(days=1))
                reason = "U" if key[0] in sources_in[following] else "D"
            out.append(
                {
                    "source_code": value[:50],
                    "source_concept_id": 0,
                    "source_code_description": (name or "")[:255],
                    "target_concept_id": key[1],
                    "target_vocabulary_id": target_vocabulary,
                    "valid_start_date": start,
                    "valid_end_date": end,
                    "invalid_reason": reason,
                }
            )
    out.sort(key=lambda r: (r["source_code"], r["target_concept_id"], r["valid_start_date"]))
    return out


def iter_source_to_concept_map(
    vocabulary_id: int, source_vocabulary_id: str, release: int | str | None = None
) -> Iterator[str]:
    """The SOURCE_TO_CONCEPT_MAP of a release (default: the latest; ``"draft"``: the current
    mappings as the next release) as CSV. A vocabulary without releases exports its mappings."""
    with session_scope() as session:
        rows = stcm_rows(session, vocabulary_id, release)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(STCM_COLUMNS)
    for i, r in enumerate(rows, 1):
        writer.writerow(
            [
                r["source_code"],
                0,
                source_vocabulary_id,
                r["source_code_description"],
                r["target_concept_id"],
                r["target_vocabulary_id"],
                r["valid_start_date"].strftime("%Y%m%d"),
                r["valid_end_date"].strftime("%Y%m%d"),
                r["invalid_reason"],
            ]
        )
        if i % 5000 == 0:
            yield buffer.getvalue()
            buffer.seek(0)
            buffer.truncate(0)
    yield buffer.getvalue()
