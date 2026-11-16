"""OMOP vocabulary import, streamed from the vocabulary directory with COPY … FROM STDIN.

Files are read as CSVs or straight from an Athena download zip (decompressed while streaming).

Behaviour is ported from the Streamlit app:
- ``concept`` is upserted through a temp table (existing concepts are updated, not deleted)
- ``concept_relationship`` and ``concept_ancestor`` are replaced completely

Unlike the Streamlit app, nothing is locked for the length of the import: concept indexes stay in
place, and the other two tables are built next to the live ones and swapped in at the end.
- files are tab-delimited with a header, using backspace as the quote character
- each table import is logged to ``vocabulary_imports``
"""

import logging
import os

import psycopg

from app.services.vocabulary_status import (
    VOCABULARY_FILES,
    check_vocabulary_files,
    open_vocabulary_file,
)
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


def _index_defs(cur: psycopg.Cursor, table: str) -> list[tuple[str, str]]:
    cur.execute(
        "SELECT indexname, indexdef FROM pg_indexes WHERE tablename = %s AND schemaname = 'public'",
        (table,),
    )
    return cur.fetchall()


def _constraint_defs(cur: psycopg.Cursor, table: str) -> list[tuple[str, str]]:
    """Foreign-key and check constraints (primary/unique keys come with their indexes)."""
    cur.execute(
        """
        SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
        WHERE conrelid = %s::regclass AND contype IN ('f', 'c')
        """,
        (f"public.{table}",),
    )
    return cur.fetchall()


def _copy_file(
    cur: psycopg.Cursor, target: str, info: dict, tracker: ByteProgress, label: str
) -> None:
    with (
        open_vocabulary_file(info) as fh,
        cur.copy(f"COPY {target} FROM STDIN {COPY_OPTIONS}") as copy,
    ):
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
    conn: psycopg.Connection, info: dict, tracker: ByteProgress, task_id: str | None
) -> int:
    """Upsert CONCEPT.csv. Indexes stay in place so searches keep working during the import;
    only rows that actually changed are written, which keeps a release-to-release update cheap."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            "CREATE TEMP TABLE temp_concept (LIKE concept INCLUDING DEFAULTS) ON COMMIT DROP"
        )
        _copy_file(cur, "temp_concept", info, tracker, "CONCEPT.csv")
        if tracker.progress:
            tracker.progress.update(message="Upserting concepts", force=True)
        cur.execute("ANALYZE temp_concept")
        cur.execute(
            """
            INSERT INTO concept AS c SELECT * FROM temp_concept
            ON CONFLICT (concept_id) DO UPDATE SET
                concept_name = EXCLUDED.concept_name, domain_id = EXCLUDED.domain_id,
                vocabulary_id = EXCLUDED.vocabulary_id, concept_class_id = EXCLUDED.concept_class_id,
                standard_concept = EXCLUDED.standard_concept, concept_code = EXCLUDED.concept_code,
                valid_start_date = EXCLUDED.valid_start_date, valid_end_date = EXCLUDED.valid_end_date,
                invalid_reason = EXCLUDED.invalid_reason
            WHERE (c.concept_name, c.domain_id, c.vocabulary_id, c.concept_class_id,
                   c.standard_concept, c.concept_code, c.valid_start_date, c.valid_end_date,
                   c.invalid_reason)
                  IS DISTINCT FROM
                  (EXCLUDED.concept_name, EXCLUDED.domain_id, EXCLUDED.vocabulary_id,
                   EXCLUDED.concept_class_id, EXCLUDED.standard_concept, EXCLUDED.concept_code,
                   EXCLUDED.valid_start_date, EXCLUDED.valid_end_date, EXCLUDED.invalid_reason)
            RETURNING (xmax = 0) AS inserted
            """
        )
        written = cur.fetchall()
        records = sum(1 for (inserted,) in written if inserted)
        logger.info("concept: %d inserted, %d updated", records, len(written) - records)
        _log_import(cur, "concept", info["path"], records, "completed", None, task_id)
    return records


def import_truncate_copy(
    conn: psycopg.Connection, table: str, info: dict, tracker: ByteProgress, task_id: str | None
) -> int:
    """Replace ``table`` with the file's contents without blocking readers.

    The data is loaded into a shadow table, indexed and constrained there, and swapped in with a
    rename. Readers keep using the old table until the swap, which takes milliseconds.
    """
    shadow = f"{table}_new"
    with conn.transaction(), conn.cursor() as cur:
        indexes = _index_defs(cur, table)
        constraints = _constraint_defs(cur, table)
        cur.execute(f"DROP TABLE IF EXISTS {shadow}")
        cur.execute(f"CREATE TABLE {shadow} (LIKE {table} INCLUDING DEFAULTS)")
        _copy_file(cur, shadow, info, tracker, VOCABULARY_FILES[table])
        if tracker.progress:
            tracker.progress.update(message=f"Building {table} indexes", force=True)
        for name, definition in indexes:
            cur.execute(
                definition.replace(f"INDEX {name} ON", f'INDEX "{name}_new" ON', 1).replace(
                    f" ON public.{table} ", f" ON public.{shadow} ", 1
                )
            )
        for name, definition in constraints:
            cur.execute(f'ALTER TABLE {shadow} ADD CONSTRAINT "{name}_new" {definition}')
        cur.execute(f"ANALYZE {shadow}")
        cur.execute(f"SELECT COUNT(*) FROM {shadow}")
        records = cur.fetchone()[0]

    if tracker.progress:
        tracker.progress.update(message=f"Swapping in the new {table}", force=True)
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("SET LOCAL lock_timeout = '60s'")
        cur.execute(f"DROP TABLE {table}")
        cur.execute(f"ALTER TABLE {shadow} RENAME TO {table}")
        for name, _ in indexes:
            cur.execute(f'ALTER INDEX "{name}_new" RENAME TO "{name}"')
        for name, _ in constraints:
            cur.execute(f'ALTER TABLE {table} RENAME CONSTRAINT "{name}_new" TO "{name}"')
        _log_import(cur, table, info["path"], records, "completed", None, task_id)
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
                records = import_concept_upsert(conn, info, tracker, task_id)
            else:
                records = import_truncate_copy(conn, table, info, tracker, task_id)
            results[table] = {"status": "success", "records_imported": records, "error": None}
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
