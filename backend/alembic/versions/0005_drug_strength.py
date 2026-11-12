"""Structured drug mapping: the OMOP ``drug_strength`` table and the parsed/provided structure of
each source drug (``source_concepts.drug_structure``).

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-12
"""

from app.core.migration_sql import run_sql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(
        """
        CREATE TABLE IF NOT EXISTS drug_strength (
            drug_concept_id INTEGER NOT NULL,
            ingredient_concept_id INTEGER NOT NULL,
            amount_value NUMERIC NULL,
            amount_unit_concept_id INTEGER NULL,
            numerator_value NUMERIC NULL,
            numerator_unit_concept_id INTEGER NULL,
            denominator_value NUMERIC NULL,
            denominator_unit_concept_id INTEGER NULL,
            box_size INTEGER NULL,
            valid_start_date DATE NOT NULL,
            valid_end_date DATE NOT NULL,
            invalid_reason TEXT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_drug_strength_drug ON drug_strength (drug_concept_id);
        CREATE INDEX IF NOT EXISTS idx_drug_strength_ingredient ON drug_strength (ingredient_concept_id);
        CREATE INDEX IF NOT EXISTS idx_concept_class_name ON concept (concept_class_id, lower(concept_name));
        ALTER TABLE source_concepts ADD COLUMN IF NOT EXISTS drug_structure JSONB NULL;
        """
    )


def downgrade() -> None:
    run_sql(
        """
        ALTER TABLE source_concepts DROP COLUMN IF EXISTS drug_structure;
        DROP INDEX IF EXISTS idx_concept_class_name;
        DROP TABLE IF EXISTS drug_strength;
        """
    )
