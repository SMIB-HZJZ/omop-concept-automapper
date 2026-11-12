"""Alembic environment.

Migrations in this project are hand-written SQL (see ``alembic/versions``); they, not the SQLModel
classes, define the schema (indexes, FK actions, the ``standard_concepts`` view, the runtime
``emb_*`` vector tables). Autogenerate is therefore disabled on purpose: comparing against the
models would propose dropping all of that. ``tests/test_schema.py`` checks that every model column
exists in the migrated database instead.
"""

from alembic import context
from app.core.db import get_engine

# None makes `alembic revision --autogenerate` fail loudly instead of generating destructive diffs
target_metadata = None


def run_migrations_online() -> None:
    connectable = context.config.attributes.get("connection")
    if connectable is not None:
        context.configure(connection=connectable, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
        return

    with get_engine().connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    raise RuntimeError("Offline migrations are not supported")
run_migrations_online()
