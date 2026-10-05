"""OMOP vocabulary tables (shape defined by the OMOP CDM, created in migration 0001)."""

from datetime import date, datetime

from sqlalchemy import Column, DateTime, Text, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlmodel import Field, SQLModel


class Concept(SQLModel, table=True):
    __tablename__ = "concept"

    concept_id: int = Field(primary_key=True)
    concept_name: str
    domain_id: str = Field(index=True)
    vocabulary_id: str = Field(index=True)
    concept_class_id: str
    standard_concept: str | None = None
    concept_code: str
    valid_start_date: date
    valid_end_date: date
    invalid_reason: str | None = None


class ConceptRelationship(SQLModel, table=True):
    """The table has no primary key; the unique index is used as the ORM identity."""

    __tablename__ = "concept_relationship"

    concept_id_1: int = Field(primary_key=True, foreign_key="concept.concept_id")
    concept_id_2: int = Field(primary_key=True, foreign_key="concept.concept_id")
    relationship_id: str = Field(primary_key=True)
    valid_start_date: date
    valid_end_date: date
    invalid_reason: str | None = None


class ConceptAncestor(SQLModel, table=True):
    __tablename__ = "concept_ancestor"

    ancestor_concept_id: int = Field(primary_key=True, foreign_key="concept.concept_id")
    descendant_concept_id: int = Field(primary_key=True, foreign_key="concept.concept_id")
    min_levels_of_separation: int
    max_levels_of_separation: int


class ConceptAtc7(SQLModel, table=True):
    __tablename__ = "concept_atc7"

    concept_id: int = Field(primary_key=True, foreign_key="concept.concept_id")
    atc7_codes: list[str] = Field(sa_column=Column(ARRAY(Text), nullable=False))
    updated_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime, server_default=func.now())
    )


class VocabularyImport(SQLModel, table=True):
    __tablename__ = "vocabulary_imports"

    import_id: int | None = Field(default=None, primary_key=True)
    table_name: str
    file_path: str
    records_imported: int = 0
    import_date: datetime | None = Field(
        default=None, sa_column=Column(DateTime, server_default=func.now())
    )
    status: str = "completed"
    error_message: str | None = None
    task_id: str | None = None
