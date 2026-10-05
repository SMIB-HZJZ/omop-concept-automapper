"""Status of the OMOP vocabulary tables and the files waiting to be imported."""

import os

from sqlalchemy import text
from sqlmodel import Session

from app.core.cache import cached
from app.core.config import get_settings

VOCABULARY_FILES = {
    "concept": "CONCEPT.csv",
    "concept_relationship": "CONCEPT_RELATIONSHIP.csv",
    "concept_ancestor": "CONCEPT_ANCESTOR.csv",
}


def check_vocabulary_files(vocabulary_dir: str | None = None) -> dict[str, dict]:
    vocabulary_dir = vocabulary_dir or get_settings().vocabulary_dir
    status = {}
    for table, filename in VOCABULARY_FILES.items():
        path = os.path.join(vocabulary_dir, filename)
        exists = os.path.isfile(path)
        status[table] = {
            "filename": filename,
            "path": path,
            "exists": exists,
            "size": os.path.getsize(path) if exists else 0,
        }
    return status


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
