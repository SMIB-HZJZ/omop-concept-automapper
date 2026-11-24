"""Application tables: users, settings, tasks and the embedding registry."""

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Column, DateTime, SmallInteger, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class AppUser(SQLModel, table=True):
    __tablename__ = "app_user"

    id: int | None = Field(default=None, primary_key=True)
    username: str = Field(unique=True)
    hashed_password: str
    role: str = "user"  # user | admin
    is_active: bool = True
    # Registered from the sign-in page and not yet approved by an admin: cannot sign in
    pending: bool = Field(default=False, sa_column_kwargs={"server_default": "false"})
    approved_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    # Bumped on password/role/active changes; tokens carrying an older version are rejected
    token_version: int = Field(default=0, sa_column_kwargs={"server_default": "0"})
    created_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), server_default=func.now())
    )
    last_login_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))


class AppConfig(SQLModel, table=True):
    __tablename__ = "app_config"

    key: str = Field(primary_key=True)
    value: str
    updated_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime, server_default=func.now(), onupdate=func.now())
    )


class TaskRun(SQLModel, table=True):
    __tablename__ = "task_run"

    id: str = Field(primary_key=True)
    kind: str = Field(index=True)
    status: str = Field(default="PENDING", index=True)
    params: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB))
    # Bigint: the vocabulary import counts bytes (a full Athena release is > 2^31)
    progress_current: int = Field(default=0, sa_column=Column(BigInteger, nullable=False))
    progress_total: int | None = Field(default=None, sa_column=Column(BigInteger))
    message: str | None = None
    result: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB))
    error: str | None = None
    user_id: int | None = Field(default=None, foreign_key="app_user.id")
    created_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), server_default=func.now())
    )
    started_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    finished_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))


class EmbeddingCollection(SQLModel, table=True):
    __tablename__ = "embedding_collection"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True)
    model: str
    dims: int
    table_name: str = Field(unique=True)
    # How the embedded text is built (see Settings.embedding_text) and whether task types are sent
    text_mode: str = "name"
    task_types: bool = False
    created_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), server_default=func.now())
    )


class DomainLabel(SQLModel, table=True):
    __tablename__ = "domain_label"

    domain_id: str = Field(primary_key=True)
    label: int = Field(sa_column=Column(SmallInteger, unique=True, nullable=False))
