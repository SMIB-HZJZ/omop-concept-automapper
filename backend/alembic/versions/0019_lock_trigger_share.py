"""The lock trigger takes a share lock on the vocabulary row, so a mapping change and a publish
cannot interleave: a change waits for a publish in progress (and then sees the lock), and a
publish waits for changes in progress (and then copies them). Before, a mapping committed just
after a publish could leave a locked vocabulary with mappings no release contains.

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-24
"""

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

LOCK_FUNCTION = """
CREATE OR REPLACE FUNCTION refuse_locked_vocabulary() RETURNS trigger AS $$
DECLARE
    vocab_id INTEGER;
    vocab_name TEXT;
    vocab_locked BOOLEAN;
BEGIN
    IF TG_TABLE_NAME = 'source_concepts' THEN
        vocab_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.source_vocabulary_id
                         ELSE NEW.source_vocabulary_id END;
    ELSE
        SELECT source_vocabulary_id INTO vocab_id FROM source_concepts
        WHERE source_id = CASE WHEN TG_OP = 'DELETE' THEN OLD.source_id
                               ELSE NEW.source_id END;
    END IF;
    -- FOR SHARE: conflicts with publish's FOR UPDATE; under read committed the row is read
    -- again once a publish commits, so a change never slips in under a new lock
    SELECT name, locked INTO vocab_name, vocab_locked FROM source_vocabulary
    WHERE id = vocab_id FOR SHARE;
    IF vocab_locked THEN
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
    op.get_bind().exec_driver_sql(LOCK_FUNCTION)


def downgrade() -> None:
    # The 0016 behaviour: the same check without the row lock
    op.get_bind().exec_driver_sql(LOCK_FUNCTION.replace(" FOR SHARE;", ";"))
