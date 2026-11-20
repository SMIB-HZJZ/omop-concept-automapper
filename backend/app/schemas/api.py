"""Request and response models for the HTTP API (they drive the generated TypeScript types)."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.atc7 import is_valid_atc7
from app.workflows.state import DEFAULT_CONFIDENCE_THRESHOLD

Role = Literal["user", "admin"]


class Message(BaseModel):
    message: str


class CountResult(BaseModel):
    count: int


# --------------------------------------------------------------------------- auth / users


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    role: Role
    is_active: bool
    created_at: datetime | None = None
    last_login_at: datetime | None = None


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=150)
    password: str = Field(min_length=1, max_length=256)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.@-]+$")
    password: str = Field(min_length=8, max_length=256)
    role: Role = "user"


class UserUpdate(BaseModel):
    role: Role | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=8, max_length=256)


class ChangePassword(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=256)


# --------------------------------------------------------------------------- meta / config


class Meta(BaseModel):
    domains: list[str]
    atc7_pattern: str
    dimension_options: list[int]
    roles: list[str]
    version: str


class ConfigValue(BaseModel):
    key: str
    value: str | int | None
    source: Literal["env", "override"]
    env_value: str | int | None
    stale_override: str | None = None


class ConfigOut(BaseModel):
    llm_model: str
    embedding_model: str
    embedding_dims: int | None
    embedding_text: str = "name_synonyms"
    embedding_task_types: bool = True
    collection_name: str
    values: list[ConfigValue]
    automap_pipeline: str
    gateway_url: str


class ConfigUpdate(BaseModel):
    llm_model: str | None = None
    embedding_model: str | None = None
    embedding_dims: int | None = None
    embedding_text: (
        Literal["name", "name_context", "name_synonyms", "name_context_synonyms"] | None
    ) = None
    embedding_task_types: bool | None = None


class EmbeddingModelOption(BaseModel):
    name: str
    max_dims: int | None = None


class ModelOptions(BaseModel):
    llm_models: list[str]
    embedding_models: list[EmbeddingModelOption]
    dimension_options: list[int]
    gateway_models_available: bool


class CollectionPreview(BaseModel):
    name: str
    exists: bool
    embedded_count: int | None = None


# --------------------------------------------------------------------------- vocabulary / atc7


class TableStatus(BaseModel):
    table_name: str
    row_count: int
    last_import: datetime | None = None
    import_count: int
    last_error: str | None = None


class VocabularyStatus(BaseModel):
    tables: list[TableStatus]
    # Largest zip the upload accepts, in bytes
    max_upload_bytes: int


class Atc7Stats(BaseModel):
    total_drugs_with_atc7: int
    avg_atc7_per_drug: float


# --------------------------------------------------------------------------- source vocabularies


class SourceVocabularyOut(BaseModel):
    id: int
    name: str
    description: str | None = None
    is_drug: bool
    code_vocabularies: list[str] = []
    created_at: datetime | None = None
    concept_count: int = 0
    mapped_count: int = 0


VOCABULARY_NAME_MAX = 100
VOCABULARY_DESCRIPTION_MAX = 500


def _clean_text(value: str | None) -> str | None:
    value = " ".join((value or "").split())
    return value or None


class SourceVocabularyCreate(BaseModel):
    # Empty: the next free id
    id: int | None = Field(default=None, ge=1)
    name: str = Field(min_length=1, max_length=VOCABULARY_NAME_MAX)
    # One or two sentences on what the terms denote; given to the LLM with every term
    description: str | None = Field(default=None, max_length=VOCABULARY_DESCRIPTION_MAX)
    is_drug: bool = False
    code_vocabularies: list[str] = []

    @field_validator("description")
    @classmethod
    def _clean_description(cls, value: str | None) -> str | None:
        return _clean_text(value)

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("Name is required")
        return value


class SourceVocabularyUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=VOCABULARY_NAME_MAX)
    description: str | None = Field(default=None, max_length=VOCABULARY_DESCRIPTION_MAX)
    is_drug: bool | None = None
    # OMOP vocabulary_ids of the codes in source_value, e.g. ["ICD10"] for MKB-10 codes
    code_vocabularies: list[str] | None = None

    @field_validator("description")
    @classmethod
    def _clean_description(cls, value: str | None) -> str | None:
        return _clean_text(value)

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Name cannot be empty")
        return " ".join(value.split()) if value else value


class UnmappedItem(BaseModel):
    source_id: int
    source_value: str
    source_concept_name: str
    source_vocabulary_id: int
    freq: int | None
    mapped_concepts: int
    atc7_codes: list[str]
    suggestion_concept_id: int | None = None
    suggestion_confidence: int | None = None
    suggestion_id: int | None = None
    flag: Literal["unmappable", "question"] | None = None
    flag_note: str | None = None
    flagged_by: str | None = None
    flagged_at: datetime | None = None


class Presence(BaseModel):
    source_id: int
    user_id: int
    username: str


class TeamActivity(BaseModel):
    user_id: int
    username: str
    mapped_today: int
    mapped_total: int
    reviewed_today: int
    reviewed_total: int
    flags_open: int


class SourceFlag(BaseModel):
    # None clears the flag
    flag: Literal["unmappable", "question"] | None
    note: str | None = Field(default=None, max_length=1000)


class UnmappedPage(BaseModel):
    items: list[UnmappedItem]
    # Flagged terms not mapped yet (in any view), for the "Flagged" tab count
    flagged: int = 0
    total: int
    page: int
    per_page: int
    pages: int


class MappedRow(BaseModel):
    source_id: int
    source_value: str
    source_concept_name: str
    concept_name: str
    concept_id: int
    domain_id: str
    vocabulary_id: str
    freq: int | None
    mapped_by: str | None = None
    mapped_at: datetime | None = None


class MappedPage(BaseModel):
    items: list[MappedRow]
    total: int
    page: int
    per_page: int
    pages: int


class DomainCount(BaseModel):
    domain_id: str
    count: int


class MappingStats(BaseModel):
    total: int
    mapped: int
    unmapped: int
    percentage: float
    total_mappings: int
    unique_concepts: int
    frequency_coverage: int
    mapped_frequency: int
    total_frequency: int
    pending_suggestions: int
    by_domain: list[DomainCount]


class AuditStat(BaseModel):
    mapping_method: str
    mapping_count: int
    avg_confidence: float | None
    min_confidence: float | None
    max_confidence: float | None


class AuditEntry(BaseModel):
    audit_id: int
    source_id: int
    source_concept_name: str
    concept_id: int
    mapped_concept_name: str
    confidence_score: float | None
    mapping_method: str
    target_domains: list[str] | None
    pipeline: str | None = None
    rationale: str | None = None
    created_by: str | None = None
    created_at: datetime | None


class MapRequest(BaseModel):
    source_id: int
    concept_ids: list[int] = Field(min_length=1)


class UnmapRequest(BaseModel):
    source_ids: list[int] = Field(min_length=1)


class SourceImportResult(BaseModel):
    inserted: int
    premapped_sources: int
    premapped_links: int
    invalid_concept_ids: list[int]


# --------------------------------------------------------------------------- concepts / search


class ConceptOut(BaseModel):
    concept_id: int
    concept_name: str
    concept_class_id: str
    domain_id: str
    vocabulary_id: str
    concept_code: str
    standard_concept: str | None = None
    invalid_reason: str | None = None
    atc7_codes: list[str] | None = None


class RelatedConcept(ConceptOut):
    relationship_id: str


class ConceptDetails(BaseModel):
    concept: ConceptOut
    synonyms: list[str]
    # Direct parents/children (one level); the counts include those beyond the shown limit
    parents: list[ConceptOut]
    parent_count: int
    children: list[ConceptOut]
    child_count: int
    # Other relationships from this concept (Maps to, Has ingredient, ...), hierarchy excluded
    related: list[RelatedConcept]


class ConceptExplanation(BaseModel):
    concept_id: int
    explanation: str
    model: str
    cached: bool = False


class ConceptLookup(BaseModel):
    found: list[ConceptOut]
    missing: list[int]


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    k: int = Field(default=50, ge=1, le=200)
    domains: list[str] = []
    vocabulary_id: str | None = None
    atc7_codes: list[str] = []

    @field_validator("atc7_codes")
    @classmethod
    def _check_atc7(cls, codes: list[str]) -> list[str]:
        cleaned = [c.strip().upper() for c in codes if c and c.strip()]
        bad = [c for c in cleaned if not is_valid_atc7(c)]
        if bad:
            raise ValueError(
                f"Invalid ATC7 code(s) {bad}: expected letter-digit-digit-letter-letter-digit-digit, e.g. A10BA02"
            )
        return cleaned


class ConceptBrief(BaseModel):
    concept_id: int
    concept_name: str
    concept_class_id: str


class SearchResult(ConceptOut):
    rank: int
    score: float


class SearchResponse(BaseModel):
    results: list[SearchResult]
    method: str
    collection: str


# --------------------------------------------------------------------------- embeddings


class EmbeddingStatus(BaseModel):
    collection: str
    model: str
    dims: int | None
    exists: bool
    points_count: int
    total: int
    embedded: int
    pending: int
    # Embedded concepts whose text changed in a vocabulary update (re-embedded by the next run)
    changed: int = 0
    percentage: float


class EmbedRequest(BaseModel):
    # Domains to embed in one run (one index build); empty = all. ``domain`` is the older
    # single-domain form.
    domains: list[str] = []
    domain: str | None = None
    # Concepts read and written per database batch (requests to the model are sized per provider)
    batch_size: int = Field(default=4000, ge=10, le=10000)


# --------------------------------------------------------------------------- tasks


class TaskRef(BaseModel):
    task_id: str
    kind: str


class TaskOut(BaseModel):
    id: str
    kind: str
    status: str
    params: dict[str, Any] | None = None
    progress_current: int
    progress_total: int | None = None
    percentage: float | None = None
    message: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    user_id: int | None = None
    username: str | None = None
    created_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    is_terminal: bool


# --------------------------------------------------------------------------- review / automap


class SuggestionOut(BaseModel):
    id: int
    source_id: int
    source_value: str
    source_concept_name: str
    freq: int | None
    concept_id: int
    concept_name: str
    domain_id: str
    vocabulary_id: str
    concept_class_id: str
    concept_code: str
    confidence: int
    method: str
    pipeline: str | None
    rationale: str | None
    match_type: str | None = None
    target_domains: list[str] | None
    status: str
    created_at: datetime | None
    decided_at: datetime | None
    decided_by: str | None
    # Combined match: further concepts accepted together with concept_id
    extra_concepts: list[ConceptBrief] = []


class SuggestionPage(BaseModel):
    items: list[SuggestionOut]
    total: int
    page: int
    per_page: int
    pages: int


class CalibrationLevel(BaseModel):
    confidence: int
    accepted: int
    rejected: int
    # Share accepted among all decided suggestions with this or a higher confidence
    precision_at_or_above: float


class CalibrationMethod(BaseModel):
    method: str
    accepted: int
    rejected: int
    precision: float | None


class Calibration(BaseModel):
    decided: int
    target_precision: float
    recommended_threshold: int | None
    levels: list[CalibrationLevel]
    methods: list[CalibrationMethod]


class SuggestionDecision(BaseModel):
    ids: list[int] | None = None
    vocabulary_id: int | None = None
    min_confidence: int | None = Field(default=None, ge=1, le=10)


# restrict: only the chosen domains; prefer: chosen domains first, a few others allowed;
# all: search every domain and let the reranker decide
DomainMode = Literal["restrict", "prefer", "all"]


class AutomapRequest(BaseModel):
    domains: list[str] = Field(min_length=1)
    threshold: int = Field(default=DEFAULT_CONFIDENCE_THRESHOLD, ge=1, le=10)
    drug_specific: bool = False
    mode: Literal["review", "commit"] = "review"
    pipeline: str | None = None
    limit: int | None = Field(default=None, ge=1)
    domain_mode: DomainMode = "prefer"


class SuggestRequest(BaseModel):
    query: str | None = None
    domains: list[str] = []
    drug_specific: bool | None = None
    atc7_codes: list[str] | None = None
    threshold: int = Field(default=DEFAULT_CONFIDENCE_THRESHOLD, ge=1, le=10)
    pipeline: str | None = None
    domain_mode: DomainMode = "prefer"


class TraceStep(BaseModel):
    node: str
    ms: float
    summary: str


class SuggestResponse(BaseModel):
    source_id: int
    query: str
    pipeline: str
    outcome: str
    selected: SearchResult | None
    # Combined match: every concept to map together (selected is the first); empty otherwise
    selected_concepts: list[SearchResult] = []
    confidence: int | None
    rationale: str | None
    match_type: str | None = None
    candidates: list[SearchResult]
    trace: list[TraceStep]


class WorkflowNodeInfo(BaseModel):
    name: str
    kind: Literal["node", "router"]
    description: str | None
    routes: list[str] | None = None
    plugin: str | None = None


class WorkflowInfo(BaseModel):
    name: str
    description: str | None
    entry: str
    nodes: dict[str, Any]
    edges: list[list[str]]
    source: str
    is_default: bool


class WorkflowCatalog(BaseModel):
    pipelines: list[WorkflowInfo]
    nodes: list[WorkflowNodeInfo]
    errors: dict[str, str]
