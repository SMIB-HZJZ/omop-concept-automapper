"""Source vocabularies, source concepts and their mappings."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Column, DateTime, Numeric, Text, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlmodel import Field, SQLModel


class SourceVocabulary(SQLModel, table=True):
    __tablename__ = "source_vocabulary"

    id: int = Field(primary_key=True)
    name: str
    description: str | None = None
    is_drug: bool = False
    created_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), server_default=func.now())
    )


class SourceConcept(SQLModel, table=True):
    __tablename__ = "source_concepts"

    source_id: int | None = Field(default=None, primary_key=True)
    source_value: str
    source_concept_name: str
    source_vocabulary_id: int = Field(foreign_key="source_vocabulary.id", index=True)
    freq: int | None = 1


class SourceStandardMap(SQLModel, table=True):
    __tablename__ = "source_standard_map"

    map_id: int | None = Field(default=None, primary_key=True)
    source_id: int = Field(foreign_key="source_concepts.source_id", index=True)
    concept_id: int = Field(foreign_key="concept.concept_id", index=True)
    created_by: int | None = Field(default=None, foreign_key="app_user.id")
    created_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), server_default=func.now())
    )


class AutoMappingAudit(SQLModel, table=True):
    __tablename__ = "auto_mapping_audit"

    audit_id: int | None = Field(default=None, primary_key=True)
    source_id: int = Field(foreign_key="source_concepts.source_id")
    concept_id: int = Field(foreign_key="concept.concept_id")
    confidence_score: Decimal | None = Field(default=None, sa_column=Column(Numeric(5, 3)))
    mapping_method: str  # auto_drug | auto_standard | manual | review_accept
    target_domains: list[str] | None = Field(default=None, sa_column=Column(ARRAY(Text)))
    pipeline: str | None = None
    task_id: str | None = None
    rationale: str | None = None
    created_by: int | None = Field(default=None, foreign_key="app_user.id")
    created_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime, server_default=func.now())
    )


class MappingSuggestion(SQLModel, table=True):
    __tablename__ = "mapping_suggestion"

    id: int | None = Field(default=None, primary_key=True)
    source_id: int = Field(foreign_key="source_concepts.source_id", index=True)
    concept_id: int = Field(foreign_key="concept.concept_id")
    confidence: int
    method: str
    pipeline: str | None = None
    target_domains: list[str] | None = Field(default=None, sa_column=Column(ARRAY(Text)))
    rationale: str | None = None
    task_id: str | None = None
    status: str = "pending"  # pending | accepted | rejected
    decided_by: int | None = Field(default=None, foreign_key="app_user.id")
    created_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), server_default=func.now())
    )
    decided_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
