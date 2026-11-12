"""Mapping aids: concept synonyms, keyword (trigram) search, exact name matching, source code
vocabularies and the match type of a decision.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-12
"""

from app.core.migration_sql import run_sql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(
        """
        CREATE EXTENSION IF NOT EXISTS pg_trgm;
        CREATE EXTENSION IF NOT EXISTS unaccent;

        -- Text normalisation for exact name matching: lower case, no accents, single spaces.
        -- unaccent() is not IMMUTABLE, so it is wrapped to allow expression indexes.
        CREATE OR REPLACE FUNCTION norm_text(t text) RETURNS text
            LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT AS
        $$ SELECT btrim(regexp_replace(lower(public.unaccent('public.unaccent'::regdictionary, t)),
                                       '[\\s[:punct:]]+', ' ', 'g')) $$;

        CREATE TABLE IF NOT EXISTS concept_synonym (
            concept_id INTEGER NOT NULL,
            concept_synonym_name TEXT NOT NULL,
            language_concept_id INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_concept_synonym_concept ON concept_synonym (concept_id);
        CREATE INDEX IF NOT EXISTS idx_concept_synonym_norm ON concept_synonym (norm_text(concept_synonym_name));
        CREATE INDEX IF NOT EXISTS idx_concept_synonym_trgm
            ON concept_synonym USING gin (lower(concept_synonym_name) gin_trgm_ops);

        CREATE INDEX IF NOT EXISTS idx_concept_norm_name ON concept (norm_text(concept_name));
        CREATE INDEX IF NOT EXISTS idx_concept_name_trgm_standard
            ON concept USING gin (lower(concept_name) gin_trgm_ops) WHERE standard_concept = 'S';
        CREATE INDEX IF NOT EXISTS idx_concept_code_vocab ON concept (concept_code, vocabulary_id);

        CREATE INDEX IF NOT EXISTS idx_source_concepts_norm_name
            ON source_concepts (norm_text(source_concept_name));

        ALTER TABLE source_vocabulary
            ADD COLUMN IF NOT EXISTS code_vocabularies TEXT[] NOT NULL DEFAULT '{}';
        ALTER TABLE mapping_suggestion ADD COLUMN IF NOT EXISTS match_type TEXT NULL;
        ALTER TABLE auto_mapping_audit ADD COLUMN IF NOT EXISTS match_type TEXT NULL;
        """
    )


def downgrade() -> None:
    run_sql(
        """
        ALTER TABLE auto_mapping_audit DROP COLUMN IF EXISTS match_type;
        ALTER TABLE mapping_suggestion DROP COLUMN IF EXISTS match_type;
        ALTER TABLE source_vocabulary DROP COLUMN IF EXISTS code_vocabularies;
        DROP INDEX IF EXISTS idx_source_concepts_norm_name;
        DROP INDEX IF EXISTS idx_concept_code_vocab;
        DROP INDEX IF EXISTS idx_concept_name_trgm_standard;
        DROP INDEX IF EXISTS idx_concept_norm_name;
        DROP TABLE IF EXISTS concept_synonym;
        DROP FUNCTION IF EXISTS norm_text(text);
        """
    )
