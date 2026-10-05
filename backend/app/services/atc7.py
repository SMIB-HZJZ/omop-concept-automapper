"""ATC7 helpers: extraction from source values, statistics and (re)computation."""

import re

from sqlalchemy import text
from sqlmodel import Session

from app.core.cache import cached

ATC7_PATTERN = r"^[A-Z]\d{2}[A-Z]{2}\d{2}$"
_ATC7_PREFIX = re.compile(r"^([A-Z]\d{2}[A-Z]{2}\d{2})")

# Ported from the Streamlit app: standard Drug concepts -> their 7-character ATC ancestors
ATC7_QUERY = """
    SELECT
        c_drug.concept_id AS drug_concept_id,
        ARRAY_AGG(DISTINCT c_atc.concept_code) AS atc_codes
    FROM concept c_drug
    JOIN concept_ancestor ca ON c_drug.concept_id = ca.descendant_concept_id
    JOIN concept c_atc ON ca.ancestor_concept_id = c_atc.concept_id
    WHERE c_drug.domain_id = 'Drug'
      AND c_drug.standard_concept = 'S'
      AND c_atc.vocabulary_id = 'ATC'
      AND LENGTH(c_atc.concept_code) = 7
    GROUP BY c_drug.concept_id
"""


def extract_atc7_codes_from_source(source_value: str | None) -> list[str]:
    """Return the ATC7 code at the very start of a source value, if any."""
    if not source_value:
        return []
    match = _ATC7_PREFIX.match(source_value.strip().upper())
    return [match.group(1)] if match else []


def is_valid_atc7(code: str) -> bool:
    return bool(re.match(ATC7_PATTERN, code.strip().upper()))


@cached("atc7")
def get_atc7_statistics(session: Session) -> dict:
    row = session.execute(
        text(
            """
            SELECT COUNT(*) AS total_drugs_with_atc7,
                   AVG(array_length(atc7_codes, 1)) AS avg_atc7_per_drug
            FROM concept_atc7
            """
        )
    ).one()
    return {
        "total_drugs_with_atc7": int(row.total_drugs_with_atc7 or 0),
        "avg_atc7_per_drug": float(row.avg_atc7_per_drug or 0),
    }


def process_drug_atc7_codes(conn) -> int:
    """Recompute ``concept_atc7`` in one transaction. ``conn`` is a raw psycopg connection."""
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute("DELETE FROM concept_atc7")
            cur.execute(
                f"INSERT INTO concept_atc7 (concept_id, atc7_codes) "
                f"SELECT drug_concept_id, atc_codes FROM ({ATC7_QUERY}) q"
            )
            return cur.rowcount
