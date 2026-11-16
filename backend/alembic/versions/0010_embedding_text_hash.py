"""Track what text each vector was embedded from, so a vocabulary update can re-embed only the
concepts whose text changed.

- ``embedding_collection.text_mode`` / ``task_types``: how the collection's text is built (was
  only encoded in the collection name)
- ``<collection table>.text_hash``: md5 of the embedded text; NULL = changed, needs re-embedding.
  Existing vectors are assumed to match the current vocabulary and get its hash.

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-16
"""

from sqlalchemy import text

from alembic import op
from app.core.migration_sql import run_sql
from app.vector.store import text_hash_sql

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

MODES = ("name_context_synonyms", "name_synonyms", "name_context")


def upgrade() -> None:
    run_sql(
        """
        ALTER TABLE embedding_collection
            ADD COLUMN IF NOT EXISTS text_mode TEXT NOT NULL DEFAULT 'name',
            ADD COLUMN IF NOT EXISTS task_types BOOLEAN NOT NULL DEFAULT false;
        """
    )
    bind = op.get_bind()
    bind.execute(
        text("UPDATE embedding_collection SET task_types = true WHERE name LIKE :p"),
        {"p": "%\\_tt"},
    )
    for mode in MODES:  # longest suffix first
        bind.execute(
            text(
                "UPDATE embedding_collection SET text_mode = :m "
                "WHERE text_mode = 'name' AND regexp_replace(name, '_tt$', '') LIKE :p"
            ),
            {"m": mode, "p": f"%\\_{mode}"},
        )
    rows = bind.execute(text("SELECT table_name, text_mode FROM embedding_collection")).all()
    for table, mode in rows:
        if not bind.execute(text("SELECT to_regclass(:t)"), {"t": table}).scalar():
            continue
        bind.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS text_hash TEXT")
        bind.exec_driver_sql(
            f"UPDATE {table} e SET text_hash = {text_hash_sql(mode)} "
            f"FROM concept c WHERE c.concept_id = e.concept_id"
        )


def downgrade() -> None:
    bind = op.get_bind()
    for (table,) in bind.execute(text("SELECT table_name FROM embedding_collection")).all():
        if bind.execute(text("SELECT to_regclass(:t)"), {"t": table}).scalar():
            bind.exec_driver_sql(f"ALTER TABLE {table} DROP COLUMN IF EXISTS text_hash")
    run_sql(
        "ALTER TABLE embedding_collection DROP COLUMN IF EXISTS text_mode, "
        "DROP COLUMN IF EXISTS task_types;"
    )
