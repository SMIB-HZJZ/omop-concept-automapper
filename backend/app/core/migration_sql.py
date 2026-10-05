"""Helpers for running plain SQL scripts in migrations."""

from alembic import op


def run_sql(script: str) -> None:
    """Execute a ';'-separated SQL script statement by statement, bypassing bind parsing."""
    bind = op.get_bind()
    for statement in _split(script):
        bind.exec_driver_sql(statement)


def _split(script: str) -> list[str]:
    statements, current = [], []
    for line in script.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        current.append(line)
        if stripped.endswith(";"):
            statements.append("\n".join(current).rstrip().rstrip(";"))
            current = []
    if current:
        statements.append("\n".join(current))
    return statements
