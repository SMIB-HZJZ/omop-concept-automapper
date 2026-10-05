"""OMOP vocabulary import, streamed from the vocabulary directory with COPY … FROM STDIN.

Behaviour is ported from the Streamlit app:
- ``concept`` is upserted through a temp table (existing concepts are updated, not deleted)
- ``concept_relationship`` and ``concept_ancestor`` are truncated and reloaded
- non-primary-key indexes are dropped during the load and recreated afterwards
- files are tab-delimited with a header, using backspace as the quote character
- each table import is logged to ``vocabulary_imports``
"""

import logging
import os

import psycopg

from app.services.vocabulary_status import VOCABULARY_FILES, check_vocabulary_files
from app.worker.progress import ProgressReporter, TaskCancelled

logger = logging.getLogger(__name__)

COPY_OPTIONS = "WITH (FORMAT csv, DELIMITER E'\\t', HEADER true, QUOTE E'\\b')"
CHUNK = 4 * 1024 * 1024


class ByteProgress:
    """Tracks bytes read across all files of one import."""

    def __init__(self, progress: ProgressReporter | None, total: int):
        self.progress = progress
        self.done = 0
        self.total = total

    def advance(self, n: int, message: str) -> None:
        self.done += n
        if self.progress:
            self.progress.update(current=self.done, total=self.total, message=message)
            self.progress.check_cancelled()


def _drop_indexes(cur: psycopg.Cursor, table: str) -> list[tuple[str, str]]:
    cur.execute(
        """
        SELECT indexname, indexdef FROM pg_indexes
        WHERE tablename = %s AND schemaname = 'public' AND indexname NOT LIKE '%%_pkey'
        """,
        (table,),
    )
    dropped = cur.fetchall()
    for name, _ in dropped:
        cur.execute(f'DROP INDEX IF EXISTS "{name}"')
    return dropped


def _recreate_indexes(cur: psycopg.Cursor, dropped: list[tuple[str, str]]) -> None:
    for name, definition in dropped:
        logger.info("Recreating index %s", name)
        cur.execute(definition)


def _copy_file(
    cur: psycopg.Cursor, target: str, path: str, tracker: ByteProgress, label: str
) -> None:
    with open(path, "rb") as fh, cur.copy(f"COPY {target} FROM STDIN {COPY_OPTIONS}") as copy:
        while chunk := fh.read(CHUNK):
            copy.write(chunk)
            tracker.advance(len(chunk), f"Loading {label}")


def _log_import(
    cur: psycopg.Cursor,
    table: str,
    path: str,
    records: int,
    status: str,
    error: str | None,
    task_id: str | None,
) -> None:
    cur.execute(
        """
        INSERT INTO vocabulary_imports (table_name, file_path, records_imported, status, error_message, task_id)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (table, path, records, status, error, task_id),
    )


def import_concept_upsert(
    conn: psycopg.Connection, path: str, tracker: ByteProgress, task_id: str | None
) -> int:
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM concept")
        before = cur.fetchone()[0]
        dropped = _drop_indexes(cur, "concept")
        cur.execute(
            "CREATE TEMP TABLE temp_concept (LIKE concept INCLUDING DEFAULTS) ON COMMIT DROP"
        )
        _copy_file(cur, "temp_concept", path, tracker, "CONCEPT.csv")
        if tracker.progress:
            tracker.progress.update(message="Upserting concepts", force=True)
        cur.execute(
            """
            INSERT INTO concept SELECT * FROM temp_concept
            ON CONFLICT (concept_id) DO UPDATE SET
                concept_name = EXCLUDED.concept_name, domain_id = EXCLUDED.domain_id,
                vocabulary_id = EXCLUDED.vocabulary_id, concept_class_id = EXCLUDED.concept_class_id,
                standard_concept = EXCLUDED.standard_concept, concept_code = EXCLUDED.concept_code,
                valid_start_date = EXCLUDED.valid_start_date, valid_end_date = EXCLUDED.valid_end_date,
                invalid_reason = EXCLUDED.invalid_reason
            """
        )
        if tracker.progress:
            tracker.progress.update(message="Rebuilding concept indexes", force=True)
        _recreate_indexes(cur, dropped)
        cur.execute("SELECT COUNT(*) FROM concept")
        after = cur.fetchone()[0]
        records = after - before
        _log_import(cur, "concept", path, records, "completed", None, task_id)
    return records


def import_truncate_copy(
    conn: psycopg.Connection, table: str, path: str, tracker: ByteProgress, task_id: str | None
) -> int:
    with conn.transaction(), conn.cursor() as cur:
        dropped = _drop_indexes(cur, table)
        cur.execute(f"TRUNCATE TABLE {table}")
        _copy_file(cur, table, path, tracker, VOCABULARY_FILES[table])
        if tracker.progress:
            tracker.progress.update(message=f"Rebuilding {table} indexes", force=True)
        _recreate_indexes(cur, dropped)
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        records = cur.fetchone()[0]
        _log_import(cur, table, path, records, "completed", None, task_id)
    return records


def import_all(
    conn: psycopg.Connection,
    progress: ProgressReporter | None = None,
    task_id: str | None = None,
    vocabulary_dir: str | None = None,
) -> dict:
    files = check_vocabulary_files(vocabulary_dir)
    total = sum(f["size"] for f in files.values() if f["exists"])
    tracker = ByteProgress(progress, total)
    results: dict[str, dict] = {}
    for table, info in files.items():
        if not info["exists"]:
            results[table] = {
                "status": "skipped",
                "records_imported": 0,
                "error": f"File not found: {info['filename']}",
            }
            continue
        try:
            if table == "concept":
                records = import_concept_upsert(conn, info["path"], tracker, task_id)
            else:
                records = import_truncate_copy(conn, table, info["path"], tracker, task_id)
            results[table] = {"status": "success", "records_imported": records, "error": None}
            with conn.cursor() as cur:
                cur.execute(f"ANALYZE {table}")
            conn.commit()
        except TaskCancelled:
            raise
        except Exception as exc:
            logger.exception("Error importing %s", table)
            conn.rollback()
            with conn.cursor() as cur:
                _log_import(cur, table, info["path"], 0, "failed", str(exc), task_id)
            conn.commit()
            results[table] = {"status": "failed", "records_imported": 0, "error": str(exc)}
            # The file was only partly read; keep the overall byte counter consistent
            tracker.done = sum(f["size"] for t, f in files.items() if f["exists"] and t in results)
    return results


def vocabulary_dir_exists(path: str) -> bool:
    return os.path.isdir(path)
