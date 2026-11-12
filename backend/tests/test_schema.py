"""The SQL migrations are the schema; the SQLModel classes must not drift from them."""

from sqlalchemy import inspect
from sqlmodel import SQLModel

import app.models  # noqa: F401  (registers the tables)
from app.core.db import get_engine


def test_every_model_column_exists_in_the_migrated_schema(client):
    inspector = inspect(get_engine())
    missing = []
    for table in SQLModel.metadata.sorted_tables:
        columns = {c["name"] for c in inspector.get_columns(table.name)}
        missing += [f"{table.name}.{c.name}" for c in table.columns if c.name not in columns]
    assert not missing, f"Model columns without a migration: {missing}"
