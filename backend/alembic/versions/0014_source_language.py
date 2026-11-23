"""Source language of a source vocabulary (ISO 639-1, e.g. "hr"); NULL lets the translation step
detect the language of each term.

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-23
"""

from app.core.migration_sql import run_sql

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(
        """
        ALTER TABLE source_vocabulary ADD COLUMN IF NOT EXISTS source_language TEXT
            CHECK (source_language IS NULL OR source_language ~ '^[a-z]{2}$');
        """
    )


def downgrade() -> None:
    run_sql("ALTER TABLE source_vocabulary DROP COLUMN IF EXISTS source_language;")
