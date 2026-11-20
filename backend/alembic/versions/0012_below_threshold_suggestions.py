"""Keep the latest auto-mapping result that fell below the confidence threshold, so the Map
list can show what the AI found and how sure it was. Status 'below_threshold' never enters the
review queue; there is at most one such row per source term.

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-20
"""

from app.core.migration_sql import run_sql

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(
        """
        ALTER TABLE mapping_suggestion DROP CONSTRAINT IF EXISTS mapping_suggestion_status_check;
        ALTER TABLE mapping_suggestion ADD CONSTRAINT mapping_suggestion_status_check
            CHECK (status IN ('pending', 'accepted', 'rejected', 'below_threshold'));
        CREATE UNIQUE INDEX IF NOT EXISTS uq_mapping_suggestion_below_threshold
            ON mapping_suggestion (source_id) WHERE status = 'below_threshold';
        """
    )


def downgrade() -> None:
    run_sql(
        """
        DELETE FROM mapping_suggestion WHERE status = 'below_threshold';
        DROP INDEX IF EXISTS uq_mapping_suggestion_below_threshold;
        ALTER TABLE mapping_suggestion DROP CONSTRAINT IF EXISTS mapping_suggestion_status_check;
        ALTER TABLE mapping_suggestion ADD CONSTRAINT mapping_suggestion_status_check
            CHECK (status IN ('pending', 'accepted', 'rejected'));
        """
    )
