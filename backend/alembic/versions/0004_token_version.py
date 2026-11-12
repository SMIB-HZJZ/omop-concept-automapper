"""Session revocation: ``app_user.token_version``.

Every JWT carries the version it was issued with. Changing a password, role or active flag bumps
the version, which invalidates the user's existing sessions immediately.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-12
"""

from app.core.migration_sql import run_sql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(
        "ALTER TABLE app_user ADD COLUMN IF NOT EXISTS token_version INTEGER NOT NULL DEFAULT 0;"
    )


def downgrade() -> None:
    run_sql("ALTER TABLE app_user DROP COLUMN IF EXISTS token_version;")
