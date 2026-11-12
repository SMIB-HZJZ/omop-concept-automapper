"""Instructor-based structured calls: the reranker and the term translator."""

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass

import instructor

from app.core.config import get_settings
from app.core.redis import get_redis
from app.llm.gateway import chat_model_settings, get_async_client
from app.llm.prompts import (
    CONCEPT_SYSTEM_PROMPT,
    DRUG_SYSTEM_PROMPT,
    INPUT_PROMPT,
    TRANSLATE_SYSTEM_PROMPT,
    TRANSLATION_LINE,
)
from app.llm.schemas import RerankerResponse, TranslationResponse

logger = logging.getLogger(__name__)

_MODES = {
    "tools": instructor.Mode.TOOLS,
    "json": instructor.Mode.JSON,
    "json_schema": instructor.Mode.JSON_SCHEMA,
    "md_json": instructor.Mode.MD_JSON,
}


def get_instructor() -> instructor.AsyncInstructor:
    """Instructor client for the running event loop (cheap: it wraps the per-loop gateway client)."""
    mode = _MODES.get(get_settings().instructor_mode.lower(), instructor.Mode.TOOLS)
    return instructor.from_openai(get_async_client(), mode=mode)


@dataclass
class RerankResult:
    index: int | None
    confidence: int | None
    rationale: str | None = None


def format_candidates(candidates: list[dict]) -> str:
    """One line per candidate: ``<id>: <name> [<class> | <domain> | <vocabulary>]``."""
    lines = []
    for i, c in enumerate(candidates):
        details = [c.get("concept_class_id"), c.get("domain_id"), c.get("vocabulary_id")]
        suffix = " | ".join(d for d in details if d)
        lines.append(f"{i}: {c['concept_name']}" + (f" [{suffix}]" if suffix else ""))
    return "\n".join(lines)


class Reranker:
    """Picks the best candidate for a source term (drug or general concept prompt)."""

    def __init__(self, model: str, client: instructor.AsyncInstructor | None = None):
        self.model = model
        # Resolved per call: the client must belong to the event loop that awaits it
        self._client = client

    async def rerank(
        self,
        term: str,
        candidates: list[dict],
        drug_specific: bool,
        translation: str | None = None,
        notes: list[str] | None = None,
    ) -> RerankResult:
        if not candidates:
            return RerankResult(None, None)
        client = self._client or get_instructor()
        response: RerankerResponse = await client.chat.completions.create(
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
                        input_term=term,
                        translation_line=(
                            TRANSLATION_LINE.format(translation=translation)
                            if translation and translation.strip().lower() != term.strip().lower()
                            else ""
                        )
                        + "".join(f"{note}\n" for note in notes or []),
                        candidate_list=format_candidates(candidates),
                    ),
                },
            ],
            max_retries=get_settings().llm_max_retries,
            context={"n_candidates": len(candidates)},
            **chat_model_settings(),
        )
        return RerankResult(
            response.most_similar_item_id, response.confidence_score, response.rationale
        )


@dataclass
class TranslationResult:
    english: str
    language: str | None = None
    cached: bool = False


class Translator:
    """Renders a source term in English OMOP wording. Results are cached in Redis per model,
    because source vocabularies repeat terms and re-runs should not pay twice."""

    CACHE_TTL = 90 * 24 * 3600

    def __init__(self, model: str, client: instructor.AsyncInstructor | None = None):
        self.model = model
        self._client = client

    def _key(self, term: str) -> str:
        digest = hashlib.sha1(term.strip().encode()).hexdigest()
        return f"translate:{self.model}:{digest}"

    async def translate(self, term: str) -> TranslationResult:
        key = self._key(term)
        try:
            hit = await asyncio.to_thread(get_redis().get, key)
            if hit:
                data = json.loads(hit)
                return TranslationResult(data["english"], data.get("language"), cached=True)
        except Exception:
            logger.debug("Translation cache read failed", exc_info=True)
        client = self._client or get_instructor()
        response: TranslationResponse = await client.chat.completions.create(
            model=self.model,
            response_model=TranslationResponse,
            messages=[
                {"role": "system", "content": TRANSLATE_SYSTEM_PROMPT},
                {"role": "user", "content": f"Term: {term}"},
            ],
            max_retries=get_settings().llm_max_retries,
            **chat_model_settings(),
        )
        result = TranslationResult(response.english.strip(), response.language)
        try:
            payload = json.dumps({"english": result.english, "language": result.language})
            await asyncio.to_thread(get_redis().set, key, payload, ex=self.CACHE_TTL)
        except Exception:
            logger.debug("Translation cache write failed", exc_info=True)
        return result
