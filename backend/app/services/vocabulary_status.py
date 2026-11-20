"""Status of the OMOP vocabulary tables, and the tables found in an uploaded Athena zip."""

import os
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from typing import IO

from sqlalchemy import text
from sqlmodel import Session

from app.core.cache import cached

VOCABULARY_FILES = {
    "concept": "CONCEPT.csv",
    "concept_relationship": "CONCEPT_RELATIONSHIP.csv",
    "concept_ancestor": "CONCEPT_ANCESTOR.csv",
    # Optional: enables deterministic drug mapping (strength and dose form matching)
    "drug_strength": "DRUG_STRENGTH.csv",
    # Optional: synonyms for exact name matching, keyword search and embedding text
    "concept_synonym": "CONCEPT_SYNONYM.csv",
}


class ArchiveError(ValueError):
    """The upload is not an Athena vocabulary zip."""


def archive_files(archive: str) -> dict[str, dict]:
    """Where each table is in an Athena download zip (at the root or in a sub-folder).

    Raises ArchiveError when the file is not a zip or contains none of the vocabulary tables."""
    try:
        with zipfile.ZipFile(archive) as zf:
            members = {
                os.path.basename(i.filename).upper(): i for i in zf.infolist() if not i.is_dir()
            }
    except (zipfile.BadZipFile, OSError) as exc:
        raise ArchiveError("The file is not a valid zip archive") from exc
    status = {}
    for table, filename in VOCABULARY_FILES.items():
        entry = members.get(filename.upper())
        status[table] = {
            "filename": filename,
            "path": f"{os.path.basename(archive)}:{entry.filename}" if entry else filename,
            "exists": entry is not None,
            "size": entry.file_size if entry else 0,  # uncompressed, for byte progress
            "archive_path": archive,
            "member": entry.filename if entry else None,
        }
    if not any(f["exists"] for f in status.values()):
        raise ArchiveError(
            "The zip contains none of the vocabulary files ("
            + ", ".join(VOCABULARY_FILES.values())
            + "). Upload the zip as downloaded from Athena."
        )
    return status


@contextmanager
def open_vocabulary_file(info: dict) -> Iterator[IO[bytes]]:
    """A binary stream of one table's file, decompressed while it is read."""
    with zipfile.ZipFile(info["archive_path"]) as zf, zf.open(info["member"]) as fh:
        yield fh


@cached("vocab")
def get_table_status(session: Session) -> list[dict]:
    imports = {
        r.table_name: r
        for r in session.execute(
            text(
                """
                SELECT table_name, MAX(import_date) AS last_import, COUNT(*) AS import_count
                FROM vocabulary_imports WHERE status = 'completed'
                GROUP BY table_name
                """
            )
        )
    }
    last_errors = {
        r.table_name: r.error_message
        for r in session.execute(
            text(
                """
                SELECT DISTINCT ON (table_name) table_name, status, error_message
                FROM vocabulary_imports ORDER BY table_name, import_date DESC
                """
            )
        )
        if r.status == "failed"
    }
    result = []
    for table in VOCABULARY_FILES:
        # Exact counts would scan millions of rows; the planner estimate is refreshed by ANALYZE
        count = session.execute(
            text("SELECT reltuples::bigint FROM pg_class WHERE oid = to_regclass(:t)"), {"t": table}
        ).scalar()
        if count is None or count < 0 or count < 100_000:
            count = session.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
        info = imports.get(table)
        result.append(
            {
                "table_name": table,
                "row_count": int(count),
                "last_import": info.last_import if info else None,
                "import_count": int(info.import_count) if info else 0,
                "last_error": last_errors.get(table),
            }
        )
    return result
