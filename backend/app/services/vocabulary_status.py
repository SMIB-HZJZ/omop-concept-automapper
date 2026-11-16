"""Status of the OMOP vocabulary tables and the files waiting to be imported."""

import glob
import os
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from typing import IO

from sqlalchemy import text
from sqlmodel import Session

from app.core.cache import cached
from app.core.config import get_settings

VOCABULARY_FILES = {
    "concept": "CONCEPT.csv",
    "concept_relationship": "CONCEPT_RELATIONSHIP.csv",
    "concept_ancestor": "CONCEPT_ANCESTOR.csv",
    # Optional: enables deterministic drug mapping (strength and dose form matching)
    "drug_strength": "DRUG_STRENGTH.csv",
    # Optional: synonyms for exact name matching, keyword search and embedding text
    "concept_synonym": "CONCEPT_SYNONYM.csv",
}


def latest_archive(vocabulary_dir: str) -> str | None:
    """The newest ``.zip`` (an Athena download) in the vocabulary directory, if any."""
    archives = [
        p
        for p in glob.glob(os.path.join(vocabulary_dir, "*"))
        if p.lower().endswith(".zip") and os.path.isfile(p)
    ]
    return max(archives, key=os.path.getmtime) if archives else None


def _archive_members(archive: str) -> dict[str, zipfile.ZipInfo]:
    """Upper-cased file name -> entry; Athena puts the CSVs at the root, but a sub-folder works."""
    try:
        with zipfile.ZipFile(archive) as zf:
            return {
                os.path.basename(i.filename).upper(): i for i in zf.infolist() if not i.is_dir()
            }
    except (zipfile.BadZipFile, OSError):
        return {}


def check_vocabulary_files(vocabulary_dir: str | None = None) -> dict[str, dict]:
    """Where each table would be imported from: ``<dir>/<FILE>.csv`` or the same file inside the
    newest zip in the directory. If both exist, the newer one wins (a fresh download is not
    shadowed by CSVs left over from the previous release)."""
    vocabulary_dir = vocabulary_dir or get_settings().vocabulary_dir
    archive = latest_archive(vocabulary_dir)
    members = _archive_members(archive) if archive else {}
    status = {}
    for table, filename in VOCABULARY_FILES.items():
        path = os.path.join(vocabulary_dir, filename)
        exists = os.path.isfile(path)
        entry = members.get(filename.upper())
        use_archive = entry is not None and (
            not exists or os.path.getmtime(archive) > os.path.getmtime(path)
        )
        if use_archive:
            status[table] = {
                "filename": filename,
                "path": f"{archive}:{entry.filename}",
                "exists": True,
                "size": entry.file_size,  # uncompressed, for byte progress
                "archive": os.path.basename(archive),
                "archive_path": archive,
                "member": entry.filename,
            }
        else:
            status[table] = {
                "filename": filename,
                "path": path,
                "exists": exists,
                "size": os.path.getsize(path) if exists else 0,
                "archive": None,
            }
    return status


@contextmanager
def open_vocabulary_file(info: dict) -> Iterator[IO[bytes]]:
    """A binary stream of one table's file, decompressed on the fly when it is in a zip."""
    if info.get("archive"):
        with zipfile.ZipFile(info["archive_path"]) as zf, zf.open(info["member"]) as fh:
            yield fh
    else:
        with open(info["path"], "rb") as fh:
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
