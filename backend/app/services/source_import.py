"""Source-concept CSV import (comma-delimited), using COPY into a staging table.

Columns: ``source_value`` and ``source_concept_name`` (required), ``freq`` (empty/NaN -> 1)
and ``concept_id`` (one or more OMOP concept ids separated by ';' used as existing mappings).
"""

import csv
import io
import re

import psycopg

from app.worker.progress import ProgressReporter

REQUIRED = ("source_value", "source_concept_name")
BATCH = 5000


class SourceImportError(ValueError):
    pass


def _parse_freq(raw: str | None) -> int:
    if raw is None:
        return 1
    raw = raw.strip()
    if not raw or raw.lower() in ("nan", "null", "none"):
        return 1
    try:
        return int(float(raw))
    except ValueError as exc:
        raise SourceImportError(f"Invalid freq value {raw!r}") from exc


def _parse_concept_ids(raw: str | None) -> list[int]:
    if not raw:
        return []
    return [int(part) for part in re.split(r"[;,]", raw) if part.strip().isdigit()]


def read_header(path: str) -> list[str]:
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return [h.strip() for h in next(csv.reader(fh), [])]


def validate_header(header: list[str]) -> None:
    missing = [c for c in REQUIRED if c not in header]
    if missing:
        raise SourceImportError(f"Missing required columns: {missing}")


def count_rows(path: str) -> int:
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return max(0, sum(1 for _ in csv.reader(fh)) - 1)


def import_source_csv(
    conn: psycopg.Connection,
    path: str,
    vocabulary_id: int,
    progress: ProgressReporter | None = None,
    user_id: int | None = None,
) -> dict:
    header = read_header(path)
    validate_header(header)
    total = count_rows(path)
    if progress:
        progress.phase("Reading CSV", total=total)

    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            """
            CREATE TEMP TABLE source_stage (
                row_no INTEGER,
                source_id INTEGER DEFAULT nextval('source_concepts_source_id_seq'),
                source_value TEXT NOT NULL,
                source_concept_name TEXT NOT NULL,
                freq INTEGER NOT NULL,
                concept_ids INTEGER[]
            ) ON COMMIT DROP
            """
        )
        with (
            open(path, encoding="utf-8-sig", newline="") as fh,
            cur.copy(
                "COPY source_stage (row_no, source_value, source_concept_name, freq, concept_ids) FROM STDIN"
            ) as copy,
        ):
            reader = csv.DictReader(fh)
            reader.fieldnames = [h.strip() for h in (reader.fieldnames or [])]
            for i, row in enumerate(reader, start=1):
                value = (row.get("source_value") or "").strip()
                name = (row.get("source_concept_name") or "").strip()
                if not value and not name:
                    continue
                if not name:
                    raise SourceImportError(f"Row {i + 1}: source_concept_name is empty")
                copy.write_row(
                    (
                        i,
                        value,
                        name,
                        _parse_freq(row.get("freq")),
                        _parse_concept_ids(row.get("concept_id")),
                    )
                )
                if progress and i % BATCH == 0:
                    progress.update(current=i)
                    progress.check_cancelled()

        if progress:
            progress.update(current=total, message="Inserting source concepts", force=True)
        cur.execute(
            """
            INSERT INTO source_vocabulary (id, name) VALUES (%s, %s) ON CONFLICT (id) DO NOTHING
            """,
            (vocabulary_id, f"Vocabulary {vocabulary_id}"),
        )
        cur.execute(
            """
            INSERT INTO source_concepts (source_id, source_value, source_concept_name, source_vocabulary_id, freq)
            SELECT source_id, source_value, source_concept_name, %s, freq FROM source_stage ORDER BY row_no
            """,
            (vocabulary_id,),
        )
        inserted = cur.rowcount

        # Existing mappings: keep only concept ids that exist in the vocabulary
        cur.execute(
            """
            SELECT DISTINCT cid FROM source_stage, unnest(concept_ids) AS cid
            WHERE NOT EXISTS (SELECT 1 FROM concept c WHERE c.concept_id = cid)
            ORDER BY cid
            """
        )
        invalid = [r[0] for r in cur.fetchall()]
        cur.execute(
            """
            INSERT INTO source_standard_map (source_id, concept_id, created_by)
            SELECT DISTINCT s.source_id, cid, %s
            FROM source_stage s CROSS JOIN LATERAL unnest(s.concept_ids) AS cid
            JOIN concept c ON c.concept_id = cid
            ON CONFLICT DO NOTHING
            """,
            (user_id,),
        )
        links = cur.rowcount
        cur.execute(
            """
            SELECT COUNT(DISTINCT s.source_id) FROM source_stage s CROSS JOIN LATERAL unnest(s.concept_ids) AS cid
            JOIN concept c ON c.concept_id = cid
            """
        )
        premapped = cur.fetchone()[0]

    return {
        "inserted": inserted,
        "premapped_sources": premapped,
        "premapped_links": links,
        "invalid_concept_ids": invalid[:1000],
    }


def preview(content: bytes, rows: int = 5) -> dict:
    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    header = [h.strip() for h in (reader.fieldnames or [])]
    sample = [row for _, row in zip(range(rows), reader, strict=False)]
    return {"columns": header, "rows": sample}
