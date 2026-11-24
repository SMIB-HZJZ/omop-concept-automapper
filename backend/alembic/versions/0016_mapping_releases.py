"""Mapping releases: a published release freezes a source vocabulary's mappings and locks it.

Each release keeps a copy of the mappings it contained, so the OMOP source_to_concept_map of a
release can show mappings of earlier releases that were later changed or removed, with an expired
valid_end_date. A trigger refuses mapping changes and new source concepts while a vocabulary is
locked, whichever code path makes them.

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-24
"""

from alembic import op
from app.core.migration_sql import run_sql

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

LOCK_FUNCTION = """
CREATE OR REPLACE FUNCTION refuse_locked_vocabulary() RETURNS trigger AS $$
DECLARE
    vocab_id INTEGER;
    vocab_name TEXT;
BEGIN
    IF TG_TABLE_NAME = 'source_concepts' THEN
        vocab_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.source_vocabulary_id
                         ELSE NEW.source_vocabulary_id END;
    ELSE
        SELECT source_vocabulary_id INTO vocab_id FROM source_concepts
        WHERE source_id = CASE WHEN TG_OP = 'DELETE' THEN OLD.source_id
                               ELSE NEW.source_id END;
    END IF;
    SELECT name INTO vocab_name FROM source_vocabulary WHERE id = vocab_id AND locked;
    IF FOUND THEN
        RAISE EXCEPTION USING
            ERRCODE = 'object_not_in_prerequisite_state',
            MESSAGE = 'Source vocabulary "' || vocab_name || '" is released and locked. '
                || 'An admin can start the next release on Check & commit to change it.';
    END IF;
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    run_sql(
        """
        ALTER TABLE source_vocabulary ADD COLUMN IF NOT EXISTS locked BOOLEAN NOT NULL DEFAULT FALSE;
        ALTER TABLE source_vocabulary ADD COLUMN IF NOT EXISTS draft_note TEXT;

        CREATE TABLE IF NOT EXISTS mapping_release (
            id SERIAL PRIMARY KEY,
            vocabulary_id INTEGER NOT NULL REFERENCES source_vocabulary(id) ON DELETE CASCADE,
            number INTEGER NOT NULL,
            released_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            released_by INTEGER REFERENCES app_user(id) ON DELETE SET NULL,
            note TEXT,
            UNIQUE (vocabulary_id, number)
        );

        -- Copies, not references: a release must not change when the source term or the
        -- vocabulary changes later
        CREATE TABLE IF NOT EXISTS mapping_release_row (
            release_id INTEGER NOT NULL REFERENCES mapping_release(id) ON DELETE CASCADE,
            source_id INTEGER NOT NULL,
            source_value TEXT NOT NULL,
            source_concept_name TEXT NOT NULL,
            concept_id INTEGER NOT NULL,
            target_vocabulary_id TEXT NOT NULL,
            PRIMARY KEY (release_id, source_id, concept_id)
        );

        """
    )
    # One statement: run_sql splits scripts at lines ending in ";"
    op.get_bind().exec_driver_sql(LOCK_FUNCTION)
    run_sql(
        """
        DROP TRIGGER IF EXISTS source_standard_map_locked ON source_standard_map;
        CREATE TRIGGER source_standard_map_locked
            BEFORE INSERT OR UPDATE OR DELETE ON source_standard_map
            FOR EACH ROW EXECUTE FUNCTION refuse_locked_vocabulary();

        DROP TRIGGER IF EXISTS source_concepts_locked ON source_concepts;
        CREATE TRIGGER source_concepts_locked
            BEFORE INSERT OR DELETE ON source_concepts
            FOR EACH ROW EXECUTE FUNCTION refuse_locked_vocabulary();
        """
    )


def downgrade() -> None:
    run_sql(
        """
        DROP TRIGGER IF EXISTS source_concepts_locked ON source_concepts;
        DROP TRIGGER IF EXISTS source_standard_map_locked ON source_standard_map;
        DROP FUNCTION IF EXISTS refuse_locked_vocabulary();
        DROP TABLE IF EXISTS mapping_release_row;
        DROP TABLE IF EXISTS mapping_release;
        ALTER TABLE source_vocabulary DROP COLUMN IF EXISTS draft_note;
        ALTER TABLE source_vocabulary DROP COLUMN IF EXISTS locked;
        """
    )
