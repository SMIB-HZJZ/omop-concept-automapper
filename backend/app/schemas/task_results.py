"""Result payloads of background tasks, one model per task kind.

``task_run.result`` is stored as JSON. These models document it in the OpenAPI schema (so the
frontend gets generated types) and are checked when a task is read, so a renamed field shows up
as a failing test instead of a silent 0 in the UI.
"""

import logging
import os

from pydantic import BaseModel, ConfigDict, ValidationError

logger = logging.getLogger(__name__)


class _Result(BaseModel):
    model_config = ConfigDict(extra="allow")


class TableImportResult(_Result):
    status: str
    records_imported: int
    error: str | None = None


class VocabularyImportResult(_Result):
    tables: dict[str, TableImportResult]
    tables_imported: int
    records_imported: int
    non_standard_mappings_removed: int
    vectors_pruned: int = 0
    vectors_relabeled: int = 0


class Atc7Result(_Result):
    drugs_with_atc7: int


class SourceImportResult(_Result):
    inserted: int
    premapped_sources: int
    premapped_links: int
    invalid_concept_ids: list[int]
    with_drug_structure: int = 0
    vocabulary_id: int
    filename: str | None = None


class EmbedResult(_Result):
    collection: str
    embedded: int
    pending_before: int | None = None
    message: str | None = None


class SyncMetadataResult(_Result):
    collection: str | None = None
    removed: int
    relabeled: int
    message: str | None = None


class SyncAtc7Result(_Result):
    collection: str | None = None
    embedded_with_atc7: int
    message: str | None = None


class AutomapResult(_Result):
    vocabulary_id: int
    total_concepts: int
    processed: int
    mapped: int
    queued: int
    skipped: int = 0
    errors: int = 0
    outcomes: dict[str, int] = {}
    mode: str | None = None
    pipeline: str | None = None
    mapping_method: str | None = None
    confidence_threshold: int | None = None
    target_domains: list[str] = []
    llm_model: str | None = None


TASK_RESULT_MODELS: dict[str, type[BaseModel]] = {
    "vocabulary.import": VocabularyImportResult,
    "atc7.process": Atc7Result,
    "source.import": SourceImportResult,
    "embeddings.embed": EmbedResult,
    "embeddings.sync_metadata": SyncMetadataResult,
    "embeddings.sync_atc7": SyncAtc7Result,
    "automap.run": AutomapResult,
}


def check_result(kind: str, result: dict | None) -> None:
    """Log (and in tests, raise) when a stored result does not match its documented model."""
    model = TASK_RESULT_MODELS.get(kind)
    if model is None or result is None:
        return
    try:
        model.model_validate(result)
    except ValidationError:
        if os.environ.get("APP_ENV") == "test":
            raise
        logger.warning("Result of a '%s' task does not match %s", kind, model.__name__)
