"""Structured-output schemas for LLM calls."""

from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator


class RerankerResponse(BaseModel):
    """The reranker's pick from a numbered candidate list.

    The prompts allow "no match", so both fields are nullable. The candidate index is checked
    against the list length passed in the validation context; Instructor re-asks on errors.
    """

    # Rationale comes first so models that do not reason internally think before they choose
    rationale: str | None = Field(
        default=None,
        max_length=500,
        description="One short sentence: the attribute(s) that decided the choice",
    )
    most_similar_item_id: int | None = Field(
        description="Id of the best-matching candidate from the list, or null if none fits"
    )
    confidence_score: int | None = Field(
        default=None,
        ge=1,
        le=10,
        description="Confidence in the match on the 1-10 scale, or null if nothing was selected",
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


class TranslationResponse(BaseModel):
    """A source term rendered in English OMOP wording."""

    english: str = Field(min_length=1, max_length=500, description="The term in English")
    language: str | None = Field(
        default=None, max_length=8, description="ISO 639-1 code of the input language"
    )


class DrugComponent(BaseModel):
    """One active ingredient with its strength, in the shape of OMOP ``drug_strength``.

    Use ``amount_*`` for solid forms (500 mg per tablet) and ``numerator_*``/``denominator_*``
    for concentrations (100 mg per 5 mL, 1 %)."""

    ingredient: str = Field(
        min_length=1,
        max_length=200,
        description="Active ingredient as its English INN (e.g. paracetamol -> acetaminophen)",
    )
    amount_value: float | None = Field(default=None, gt=0)
    amount_unit: str | None = Field(default=None, max_length=20)
    numerator_value: float | None = Field(default=None, gt=0)
    numerator_unit: str | None = Field(default=None, max_length=20)
    denominator_value: float | None = Field(default=None, gt=0)
    denominator_unit: str | None = Field(default=None, max_length=20)

    @property
    def has_strength(self) -> bool:
        return self.amount_value is not None or self.numerator_value is not None


class DrugStructure(BaseModel):
    """A drug product broken into ingredients, strengths and dose form."""

    components: list[DrugComponent] = Field(default_factory=list, max_length=20)
    dose_form: str | None = Field(
        default=None, max_length=100, description="RxNorm dose form name, e.g. 'Oral Tablet'"
    )
    brand_name: str | None = Field(default=None, max_length=100)


class ParsedDrug(DrugStructure):
    """LLM output: the structure, or ``is_drug=false`` for terms that are not a drug product."""

    is_drug: bool = Field(description="False if the term is not a medicinal product")
