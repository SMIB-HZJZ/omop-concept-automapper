"""Trigram index on source codes, so the term searches (code or name contains the text) use
indexes on both columns instead of reading every term of the vocabulary.

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-24
"""

from app.core.migration_sql import run_sql

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(
        """
        CREATE INDEX IF NOT EXISTS idx_source_concepts_value_trgm
            ON source_concepts USING gin (lower(source_value) gin_trgm_ops);
        """
    )


def downgrade() -> None:
    run_sql("DROP INDEX IF EXISTS idx_source_concepts_value_trgm;")
