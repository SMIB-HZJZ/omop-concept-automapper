"""Task progress as bigint: the vocabulary import reports bytes, and a full Athena release is
about 3.5 GB, more than an integer holds.

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-16
"""

from app.core.migration_sql import run_sql

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(
        "ALTER TABLE task_run ALTER COLUMN progress_current TYPE bigint, "
        "ALTER COLUMN progress_total TYPE bigint;"
    )


def downgrade() -> None:
    run_sql(
        "ALTER TABLE task_run ALTER COLUMN progress_current TYPE integer, "
        "ALTER COLUMN progress_total TYPE integer;"
    )
