"""How an AI suggestion came about, e.g. "AI pick 6 -> agent retry 7 -> final decision 8", shown in
Review so mappers (and the precision statistics) can tell which pipeline step decided.

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-20
"""

from app.core.migration_sql import run_sql

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql("ALTER TABLE mapping_suggestion ADD COLUMN IF NOT EXISTS decision_path TEXT;")


def downgrade() -> None:
    run_sql("ALTER TABLE mapping_suggestion DROP COLUMN IF EXISTS decision_path;")
