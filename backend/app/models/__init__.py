"""All table models, imported here so SQLModel.metadata is complete."""

from app.models.source import (
    AutoMappingAudit,
    MappingSuggestion,
    SourceConcept,
    SourceStandardMap,
    SourceVocabulary,
)
from app.models.system import (
    AppConfig,
    AppUser,
    DomainLabel,
    EmbeddingCollection,
    TaskRun,
)
from app.models.vocabulary import (
    Concept,
    ConceptAncestor,
    ConceptAtc7,
    ConceptRelationship,
    DrugStrength,
    VocabularyImport,
)

__all__ = [
    "AppConfig",
    "AppUser",
    "AutoMappingAudit",
    "Concept",
    "ConceptAncestor",
    "ConceptAtc7",
    "ConceptRelationship",
    "DomainLabel",
    "DrugStrength",
    "EmbeddingCollection",
    "MappingSuggestion",
    "SourceConcept",
    "SourceStandardMap",
    "SourceVocabulary",
    "TaskRun",
    "VocabularyImport",
]
