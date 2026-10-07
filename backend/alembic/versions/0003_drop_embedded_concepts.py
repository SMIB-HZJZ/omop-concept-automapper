"""Drop the Qdrant-era ``embedded_concepts`` tracking table.

Embedding status now comes from the pgvector collection tables themselves (see
``app.vector.store.embedding_status``), and the Qdrant migration task copies every point
without consulting this table.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-05
"""

from app.core.migration_sql import run_sql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql("DROP TABLE IF EXISTS embedded_concepts;")


def downgrade() -> None:
    run_sql(
        """
        CREATE TABLE IF NOT EXISTS embedded_concepts (
            concept_id INTEGER NOT NULL,
            collection_name TEXT NOT NULL,
            embedding_model TEXT,
            embedded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            concept_type TEXT NOT NULL DEFAULT 'standard',
            source_vocabulary_id INTEGER NULL,
            PRIMARY KEY (concept_id, collection_name, concept_type)
        );
        """
    )
