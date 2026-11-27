"""OMOP VOCABULARY table, imported from VOCABULARY.csv. Its 'None' row holds the version of the
whole vocabulary release, shown on the Import page.

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-27
"""

from app.core.migration_sql import run_sql

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(
        """
        CREATE TABLE IF NOT EXISTS vocabulary (
            vocabulary_id TEXT PRIMARY KEY,
            vocabulary_name TEXT,
            vocabulary_reference TEXT,
            vocabulary_version TEXT,
            vocabulary_concept_id INTEGER
        );
        """
    )


def downgrade() -> None:
    run_sql("DROP TABLE IF EXISTS vocabulary;")
