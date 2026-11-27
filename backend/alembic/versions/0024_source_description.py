"""Source terms can carry a description from the source list (e.g. when clinicians should use the
code), shown to reviewers and given to the LLM steps.

Revision ID: 0024
Revises: 0023
Create Date: 2026-11-27
"""

from app.core.migration_sql import run_sql

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql("ALTER TABLE source_concepts ADD COLUMN IF NOT EXISTS source_description TEXT NULL;")


def downgrade() -> None:
    run_sql("ALTER TABLE source_concepts DROP COLUMN IF EXISTS source_description;")
