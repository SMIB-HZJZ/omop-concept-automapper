"""How a mapped concept relates to its source term (exact, broader, narrower, related), kept with
the mapping itself and in release copies. Until now it was only on suggestions and the audit
trail; existing mappings take it from their latest audit row (NULL when unknown, e.g. imported).

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-24
"""

from app.core.migration_sql import run_sql

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(
        """
        ALTER TABLE source_standard_map ADD COLUMN IF NOT EXISTS match_type TEXT
            CHECK (match_type IN ('exact', 'broader', 'narrower', 'related'));
        ALTER TABLE mapping_release_row ADD COLUMN IF NOT EXISTS match_type TEXT;

        -- Backfill; the lock trigger would refuse updates on released vocabularies
        ALTER TABLE source_standard_map DISABLE TRIGGER source_standard_map_locked;
        UPDATE source_standard_map m SET match_type = a.match_type
        FROM (SELECT DISTINCT ON (source_id, concept_id) source_id, concept_id, match_type
              FROM auto_mapping_audit
              ORDER BY source_id, concept_id, created_at DESC, audit_id DESC) a
        WHERE a.source_id = m.source_id AND a.concept_id = m.concept_id
          AND a.match_type IN ('exact', 'broader', 'narrower', 'related');
        ALTER TABLE source_standard_map ENABLE TRIGGER source_standard_map_locked;

        UPDATE mapping_release_row r SET match_type = m.match_type
        FROM source_standard_map m
        WHERE m.source_id = r.source_id AND m.concept_id = r.concept_id;
        """
    )


def downgrade() -> None:
    run_sql(
        """
        ALTER TABLE mapping_release_row DROP COLUMN IF EXISTS match_type;
        ALTER TABLE source_standard_map DROP COLUMN IF EXISTS match_type;
        """
    )
