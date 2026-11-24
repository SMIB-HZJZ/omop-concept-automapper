"""Self-registration: accounts created from the sign-in page wait for an admin's approval.

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-24
"""

from app.core.migration_sql import run_sql

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing accounts were created by an admin: they are approved
    run_sql(
        """
        ALTER TABLE app_user ADD COLUMN IF NOT EXISTS pending BOOLEAN NOT NULL DEFAULT FALSE;
        ALTER TABLE app_user ADD COLUMN IF NOT EXISTS approved_at TIMESTAMPTZ;
        """
    )


def downgrade() -> None:
    run_sql(
        """
        ALTER TABLE app_user DROP COLUMN IF EXISTS approved_at;
        ALTER TABLE app_user DROP COLUMN IF EXISTS pending;
        """
    )
