"""Database engine and session helpers (SQLModel on psycopg3)."""

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from sqlalchemy import Engine
from sqlmodel import Session, create_engine

from app.core.config import get_settings

_engine: Engine | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_engine(
            settings.database_url,
            pool_pre_ping=True,
            pool_size=10,
            max_overflow=20,
        )
    return _engine


def reset_engine() -> None:
    """Dispose the engine (used after fork in Celery workers and in tests)."""
    global _engine
    if _engine is not None:
        _engine.dispose()
    _engine = None


def get_session() -> Iterator[Session]:
    """FastAPI dependency yielding a session."""
    with Session(get_engine()) as session:
        yield session


@contextmanager
def session_scope() -> Iterator[Session]:
    """Session for background code; commits on success, rolls back on error.

    Objects stay readable after the commit (``expire_on_commit=False``)."""
    with Session(get_engine(), expire_on_commit=False) as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


@contextmanager
def raw_connection() -> Iterator[psycopg.Connection]:
    """A pooled raw psycopg connection, for COPY and other driver-level operations.

    The caller controls transactions; anything uncommitted is rolled back on exit.
    """
    pooled = get_engine().raw_connection()
    try:
        conn: psycopg.Connection = pooled.driver_connection  # type: ignore[assignment]
        yield conn
    except Exception:
        pooled.rollback()
        raise
    finally:
        pooled.close()
