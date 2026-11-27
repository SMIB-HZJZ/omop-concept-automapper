"""Manual mappings decide the AI picks they supersede. A pending or below-threshold suggestion for
a term that is mapped by hand becomes accepted (same concepts) or rejected (other concepts), so
threshold calibration also learns from terms that never reached Review. ``prior_status`` keeps
the status the pick had before, so unmapping the term (Undo) can put it back.

Revision ID: 0021
Revises: 0020
Create Date: 2026-11-27
"""

from app.core.migration_sql import run_sql

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql("ALTER TABLE mapping_suggestion ADD COLUMN IF NOT EXISTS prior_status TEXT NULL;")


def downgrade() -> None:
    run_sql("ALTER TABLE mapping_suggestion DROP COLUMN IF EXISTS prior_status;")
