"""Combined suggestions: a suggestion can map one source concept to several concepts together
(e.g. one Ingredient per ingredient of a combination product).

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-12
"""

from app.core.migration_sql import run_sql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(
        "ALTER TABLE mapping_suggestion "
        "ADD COLUMN IF NOT EXISTS extra_concept_ids INTEGER[] NOT NULL DEFAULT '{}';"
    )


def downgrade() -> None:
    run_sql("ALTER TABLE mapping_suggestion DROP COLUMN IF EXISTS extra_concept_ids;")
