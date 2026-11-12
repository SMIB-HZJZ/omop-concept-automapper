"""Trigram index on source concept names: similar, already mapped terms are shown to the
reranker as examples.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-12
"""

from app.core.migration_sql import run_sql

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(
        "CREATE INDEX IF NOT EXISTS idx_source_concepts_name_trgm "
        "ON source_concepts USING gin (lower(source_concept_name) gin_trgm_ops);"
    )


def downgrade() -> None:
    run_sql("DROP INDEX IF EXISTS idx_source_concepts_name_trgm;")
