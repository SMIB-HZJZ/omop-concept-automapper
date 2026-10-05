"""Structured-output schemas for LLM calls."""

from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator


class RerankerResponse(BaseModel):
    """The reranker's pick from a numbered candidate list.

    The prompts allow "no match", so both fields are nullable. The candidate index is checked
    against the list length passed in the validation context; Instructor re-asks on errors.
    """

    most_similar_item_id: int | None = Field(
        description="Index of the most similar item from the candidate list, or null if nothing matches"
    )
    confidence_score: int | None = Field(
        default=None,
        ge=1,
        le=10,
        description="Confidence in the match from 1 to 10, or null if no match",
    )

    @field_validator("most_similar_item_id")
    @classmethod
    def _index_in_range(cls, value: int | None, info: ValidationInfo) -> int | None:
        n = (info.context or {}).get("n_candidates")
        if value is not None and n is not None and not 0 <= value < n:
            raise ValueError(
                f"most_similar_item_id must be one of the listed ids (0 to {n - 1}) or null, got {value}"
            )
        return value

    @model_validator(mode="after")
    def _confidence_with_match(self) -> "RerankerResponse":
        if self.most_similar_item_id is not None and self.confidence_score is None:
            raise ValueError("confidence_score is required when an item is selected")
        return self


class MappingDecision(BaseModel):
    """Output of the Pydantic AI mapping agent."""

    concept_id: int | None = Field(
        description="The chosen standard OMOP concept_id, or null if none fits"
    )
    confidence: int | None = Field(default=None, ge=1, le=10)
    rationale: str = Field(description="One sentence explaining the decision")
