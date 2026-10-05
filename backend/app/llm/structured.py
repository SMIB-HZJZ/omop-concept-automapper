"""Instructor-based structured calls (the reranker)."""

import logging
from dataclasses import dataclass
from functools import lru_cache

import instructor

from app.core.config import get_settings
from app.llm.gateway import chat_model_settings, get_async_client
from app.llm.prompts import CONCEPT_SYSTEM_PROMPT, DRUG_SYSTEM_PROMPT, INPUT_PROMPT
from app.llm.schemas import RerankerResponse

logger = logging.getLogger(__name__)

_MODES = {
    "tools": instructor.Mode.TOOLS,
    "json": instructor.Mode.JSON,
    "json_schema": instructor.Mode.JSON_SCHEMA,
    "md_json": instructor.Mode.MD_JSON,
}


@lru_cache
def get_instructor() -> instructor.AsyncInstructor:
    mode = _MODES.get(get_settings().instructor_mode.lower(), instructor.Mode.TOOLS)
    return instructor.from_openai(get_async_client(), mode=mode)


@dataclass
class RerankResult:
    index: int | None
    confidence: int | None


def format_candidates(candidates: list[dict]) -> str:
    return "\n".join(f"{i}: {c['concept_name']}" for i, c in enumerate(candidates))


class Reranker:
    """Picks the best candidate for a source term (drug or general concept prompt)."""

    def __init__(self, model: str, client: instructor.AsyncInstructor | None = None):
        self.model = model
        self.client = client or get_instructor()

    async def rerank(self, term: str, candidates: list[dict], drug_specific: bool) -> RerankResult:
        if not candidates:
            return RerankResult(None, None)
        response: RerankerResponse = await self.client.chat.completions.create(
            model=self.model,
            response_model=RerankerResponse,
            messages=[
                {
                    "role": "system",
                    "content": DRUG_SYSTEM_PROMPT if drug_specific else CONCEPT_SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": INPUT_PROMPT.format(
                        input_term=term, candidate_list=format_candidates(candidates)
                    ),
                },
            ],
            max_retries=get_settings().llm_max_retries,
            context={"n_candidates": len(candidates)},
            **chat_model_settings(),
        )
        return RerankResult(response.most_similar_item_id, response.confidence_score)
