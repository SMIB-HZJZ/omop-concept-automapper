"""Flags on source terms: "unmappable" or "question" (ask a colleague), with a note, who and when.
Flagged terms leave the default to-do list and auto-mapping skips them.

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-19
"""

from app.core.migration_sql import run_sql

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(
        """
        ALTER TABLE source_concepts
            ADD COLUMN IF NOT EXISTS flag TEXT
                CHECK (flag IS NULL OR flag IN ('unmappable', 'question')),
            ADD COLUMN IF NOT EXISTS flag_note TEXT,
            ADD COLUMN IF NOT EXISTS flagged_by INTEGER REFERENCES app_user(id) ON DELETE SET NULL,
            ADD COLUMN IF NOT EXISTS flagged_at TIMESTAMPTZ;
        CREATE INDEX IF NOT EXISTS idx_source_concepts_flag
            ON source_concepts (source_vocabulary_id) WHERE flag IS NOT NULL;
        """
    )


def downgrade() -> None:
    run_sql(
        """
        DROP INDEX IF EXISTS idx_source_concepts_flag;
        ALTER TABLE source_concepts DROP COLUMN IF EXISTS flag, DROP COLUMN IF EXISTS flag_note,
            DROP COLUMN IF EXISTS flagged_by, DROP COLUMN IF EXISTS flagged_at;
        """
    )
