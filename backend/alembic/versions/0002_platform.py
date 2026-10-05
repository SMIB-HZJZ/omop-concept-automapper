"""Platform schema: users, source vocabularies, review queue, tasks, pgvector registry.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-05
"""

from app.core.migration_sql import run_sql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

UPGRADE_SQL = """
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS vectorscale CASCADE;

-- Users and roles
CREATE TABLE app_user (
    id SERIAL PRIMARY KEY,
    username TEXT NOT NULL,
    hashed_password TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('user', 'admin')),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT now(),
    last_login_at TIMESTAMPTZ
);
CREATE UNIQUE INDEX uq_app_user_username ON app_user (lower(username));

-- I1: source vocabularies become a real entity (ids are kept)
CREATE TABLE source_vocabulary (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    is_drug BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT now()
);
INSERT INTO source_vocabulary (id, name)
SELECT DISTINCT source_vocabulary_id, 'Vocabulary ' || source_vocabulary_id
FROM source_concepts;
ALTER TABLE source_concepts
    ADD CONSTRAINT fk_source_concepts_vocabulary
    FOREIGN KEY (source_vocabulary_id) REFERENCES source_vocabulary (id) ON DELETE CASCADE;
ALTER TABLE source_concepts DROP COLUMN IF EXISTS mapped;
CREATE INDEX IF NOT EXISTS idx_source_vocab_freq ON source_concepts (source_vocabulary_id, freq DESC);

-- Mappings: no duplicate pairs, plus attribution
DELETE FROM source_standard_map a
USING source_standard_map b
WHERE a.map_id > b.map_id AND a.source_id = b.source_id AND a.concept_id = b.concept_id;
ALTER TABLE source_standard_map
    ADD CONSTRAINT uq_source_standard_map UNIQUE (source_id, concept_id);
ALTER TABLE source_standard_map
    ADD COLUMN created_at TIMESTAMPTZ DEFAULT now(),
    ADD COLUMN created_by INTEGER REFERENCES app_user (id) ON DELETE SET NULL;

ALTER TABLE auto_mapping_audit
    ADD COLUMN pipeline TEXT,
    ADD COLUMN task_id TEXT,
    ADD COLUMN rationale TEXT,
    ADD COLUMN created_by INTEGER REFERENCES app_user (id) ON DELETE SET NULL;

ALTER TABLE vocabulary_imports ADD COLUMN task_id TEXT;

-- I2: review queue
CREATE TABLE mapping_suggestion (
    id SERIAL PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES source_concepts (source_id) ON DELETE CASCADE,
    concept_id INTEGER NOT NULL REFERENCES concept (concept_id) ON DELETE CASCADE,
    confidence INTEGER NOT NULL,
    method TEXT NOT NULL,
    pipeline TEXT,
    target_domains TEXT[],
    rationale TEXT,
    task_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'accepted', 'rejected')),
    decided_by INTEGER REFERENCES app_user (id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ DEFAULT now(),
    decided_at TIMESTAMPTZ
);
CREATE UNIQUE INDEX uq_mapping_suggestion_pending ON mapping_suggestion (source_id) WHERE status = 'pending';
CREATE INDEX ix_mapping_suggestion_status ON mapping_suggestion (status, confidence DESC);
CREATE INDEX ix_mapping_suggestion_source ON mapping_suggestion (source_id);

-- Background task records
CREATE TABLE task_run (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    params JSONB,
    progress_current INTEGER NOT NULL DEFAULT 0,
    progress_total INTEGER,
    message TEXT,
    result JSONB,
    error TEXT,
    user_id INTEGER REFERENCES app_user (id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ DEFAULT now(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ
);
CREATE INDEX ix_task_run_kind ON task_run (kind);
CREATE INDEX ix_task_run_status ON task_run (status);
CREATE INDEX ix_task_run_created ON task_run (created_at DESC);

-- Embedding spaces (one pgvector table per model + dims)
CREATE TABLE embedding_collection (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    model TEXT NOT NULL,
    dims INTEGER NOT NULL,
    table_name TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- OMOP domain -> smallint label used by DiskANN filtered search
CREATE TABLE domain_label (
    domain_id TEXT PRIMARY KEY,
    label SMALLINT NOT NULL UNIQUE
);

-- Settings from the Streamlit app are kept for reference under legacy.* and no longer read
UPDATE app_config SET key = 'legacy.' || key
WHERE key IN ('vector_store.name', 'vector_store.embeddings', 'vector_store.dims',
              'vector_store.url', 'reranker.model');
"""

DOWNGRADE_SQL = """
UPDATE app_config SET key = substring(key from 8) WHERE starts_with(key, 'legacy.');
DROP TABLE IF EXISTS domain_label;
DROP TABLE IF EXISTS embedding_collection;
DROP TABLE IF EXISTS task_run;
DROP TABLE IF EXISTS mapping_suggestion;
ALTER TABLE vocabulary_imports DROP COLUMN IF EXISTS task_id;
ALTER TABLE auto_mapping_audit DROP COLUMN IF EXISTS pipeline, DROP COLUMN IF EXISTS task_id,
    DROP COLUMN IF EXISTS rationale, DROP COLUMN IF EXISTS created_by;
ALTER TABLE source_standard_map DROP CONSTRAINT IF EXISTS uq_source_standard_map;
ALTER TABLE source_standard_map DROP COLUMN IF EXISTS created_at, DROP COLUMN IF EXISTS created_by;
DROP INDEX IF EXISTS idx_source_vocab_freq;
ALTER TABLE source_concepts ADD COLUMN IF NOT EXISTS mapped BOOLEAN DEFAULT FALSE;
ALTER TABLE source_concepts DROP CONSTRAINT IF EXISTS fk_source_concepts_vocabulary;
DROP TABLE IF EXISTS source_vocabulary;
DROP TABLE IF EXISTS app_user;
"""


def upgrade() -> None:
    run_sql(UPGRADE_SQL)


def downgrade() -> None:
    run_sql(DOWNGRADE_SQL)
