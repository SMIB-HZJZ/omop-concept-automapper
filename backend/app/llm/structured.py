"""Instructor-based structured calls: the reranker and the term translator."""

import asyncio
import dataclasses
import hashlib
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field

import instructor

from app.core.config import get_settings
from app.core.redis import get_redis
from app.llm.gateway import chat_model_settings, get_async_client
from app.llm.prompts import (
    CONCEPT_SYSTEM_PROMPT,
    DRUG_SYSTEM_PROMPT,
    EXAMPLES_BLOCK,
    INPUT_PROMPT,
    REWRITE_SYSTEM_PROMPT,
    SOURCE_LANGUAGE_LINE,
    TERM_DESCRIPTION_LINE,
    TRANSLATE_SYSTEM_PROMPT,
    TRANSLATION_LINE,
    VOCABULARY_LINE,
)
from app.llm.schemas import QueryRewriteResponse, RerankerResponse, TranslationResponse

logger = logging.getLogger(__name__)

_MODES = {
    "tools": instructor.Mode.TOOLS,
    "json": instructor.Mode.JSON,
    "json_schema": instructor.Mode.JSON_SCHEMA,
    "md_json": instructor.Mode.MD_JSON,
}


def instructor_mode_for(model: str | None) -> instructor.Mode:
    """INSTRUCTOR_MODE, or the INSTRUCTOR_MODE_OVERRIDES entry with the longest matching prefix."""
    settings = get_settings()
    name = settings.instructor_mode
    best = -1
    for item in settings.instructor_mode_overrides.split(","):
        prefix, _, mode = item.strip().partition("=")
        if mode and model and model.startswith(prefix.strip()) and len(prefix) > best:
            name, best = mode.strip(), len(prefix)
    return _MODES.get(name.lower(), instructor.Mode.TOOLS)


def get_instructor(model: str | None = None) -> instructor.AsyncInstructor:
    """Instructor client for the running event loop (cheap: it wraps the per-loop gateway client)."""
    return instructor.from_openai(get_async_client(), mode=instructor_mode_for(model))


@dataclass
class RerankResult:
    index: int | None
    confidence: int | None
    rationale: str | None = None
    match_type: str | None = None
    additional: list[int] = field(default_factory=list)
    # Cascade only: "agreed" (the voters picked the same candidate) or "judge" (they did not)
    route: str | None = None
    votes: list[dict] = field(default_factory=list)


# Short forms in relationship ids (SNOMED, RxNorm)
_ATTRIBUTE_WORDS = {
    "ing": "ingredient",
    "asso": "associated",
    "morph": "morphology",
    "dir": "direct",
}


def attribute_name(relationship_id: str) -> str:
    """'Has finding site' -> 'finding site', 'RxNorm has ing' -> 'ingredient'."""
    words = re.sub(r"(?i)^.*?\bhas\s+", "", relationship_id).lower().split()
    return " ".join(_ATTRIBUTE_WORDS.get(w, w) for w in words)


def format_candidates(candidates: list[dict]) -> str:
    """One line per candidate: ``<id>: <name> [<class> | <domain> | <vocabulary>]``, followed by
    indented lines with its parents, attributes and synonyms when it carries a ``context``."""
    lines = []
    for i, c in enumerate(candidates):
        details = [c.get("concept_class_id"), c.get("domain_id"), c.get("vocabulary_id")]
        suffix = " | ".join(d for d in details if d)
        lines.append(f"{i}: {c['concept_name']}" + (f" [{suffix}]" if suffix else ""))
        context = c.get("context") or {}
        if context.get("parents"):
            lines.append("   is a: " + "; ".join(context["parents"]))
        if context.get("attributes"):
            lines.append(
                "   "
                + "; ".join(f"{attribute_name(rel)}: {name}" for rel, name in context["attributes"])
            )
        if context.get("synonyms"):
            lines.append("   also: " + "; ".join(context["synonyms"]))
    return "\n".join(lines)


def model_label(model: str, effort: str | None) -> str:
    return f"{model} ({effort} effort)" if effort else model


class Reranker:
    """Picks the best candidate for a source term (drug or general concept prompt)."""

    def __init__(
        self,
        model: str,
        client: instructor.AsyncInstructor | None = None,
        effort: str | None = None,
    ):
        self.model = model
        self.effort = effort or None
        self.label = model_label(model, self.effort)
        # Resolved per call: the client must belong to the event loop that awaits it
        self._client = client

    async def rerank(
        self,
        term: str,
        candidates: list[dict],
        drug_specific: bool,
        translation: str | None = None,
        notes: list[str] | None = None,
        examples: list[dict] | None = None,
        context: str | None = None,
    ) -> RerankResult:
        if not candidates:
            return RerankResult(None, None)
        messages = [
            {
                "role": "system",
                "content": DRUG_SYSTEM_PROMPT if drug_specific else CONCEPT_SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": INPUT_PROMPT.format(
                    examples=(
                        EXAMPLES_BLOCK.format(
                            lines="\n".join(
                                f"- {e['source_name']} -> {e['mapped_to']}" for e in examples
                            )
                        )
                        if examples
                        else ""
                    ),
                    vocabulary_line=VOCABULARY_LINE.format(context=context) if context else "",
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
        ]
        # Same prompt, same model and effort -> reuse the answer across runs
        ident = [self.model, messages] if not self.effort else [self.model, self.effort, messages]
        key = "rerank:" + hashlib.sha1(json.dumps(ident, sort_keys=True).encode()).hexdigest()
        ttl = get_settings().llm_cache_ttl
        try:
            hit = await asyncio.to_thread(get_redis().get, key) if ttl else None
            if hit:
                return RerankResult(**json.loads(hit))
        except Exception:
            logger.debug("Rerank cache read failed", exc_info=True)
        client = self._client or get_instructor(self.model)
        response: RerankerResponse = await client.chat.completions.create(
            model=self.model,
            response_model=RerankerResponse,
            messages=messages,
            max_retries=get_settings().llm_max_retries,
            context={"n_candidates": len(candidates)},
            **chat_model_settings(self.model, self.effort),
        )
        result = RerankResult(
            response.most_similar_item_id,
            response.confidence_score,
            response.rationale,
            response.match_type,
            [i for i in response.additional_item_ids if i != response.most_similar_item_id]
            if response.most_similar_item_id is not None
            else [],
        )
        try:
            if ttl:
                await asyncio.to_thread(
                    get_redis().set, key, json.dumps(dataclasses.asdict(result)), ex=ttl
                )
        except Exception:
            logger.debug("Rerank cache write failed", exc_info=True)
        return result


def votes_note(votes: list[dict], candidates: list[dict]) -> str:
    """Tells the judge what the voters picked, without naming the models."""
    lines = []
    for n, v in enumerate(votes, 1):
        if v.get("error"):
            pick = "no answer (error)"
        elif v["index"] is None:
            pick = "nothing (no candidate fits)"
        else:
            pick = f"{v['index']}: {candidates[v['index']]['concept_name']} (confidence {v['confidence']})"
        lines.append(f"- Reviewer {n}: {pick}")
    return (
        "Other reviewers disagreed on this term. Their picks (they may all be wrong; "
        "decide independently under the rules):\n" + "\n".join(lines)
    )


class EscalatingReranker:
    """The first reranker decides; when it is unsure (confidence from ``from_confidence`` up to
    below ``below``, or an error) the stronger model reranks the term from scratch and its answer
    is used. With ``from_confidence`` set, a very unsure first answer or none stands (later
    pipeline steps search again instead).

    The stronger model gets the same prompt, not the first pick: in the evaluation, showing
    other models' picks to the deciding model did not help."""

    def __init__(
        self,
        first: "Reranker | CascadeReranker",
        stronger: Reranker,
        below: int,
        from_confidence: int = 0,
    ):
        self.first = first
        self.stronger = stronger
        self.below = below
        # A pick under this, or no pick, usually means the right concept is not among the
        # candidates: a stronger model cannot fix that, a new search (rewrite_search) can
        self.from_confidence = from_confidence
        self.model = f"{first.model} → {stronger.model} (confidence < {below})"
        self.label = f"{first.label} → {stronger.label} (confidence < {below})"

    async def rerank(
        self,
        term: str,
        candidates: list[dict],
        drug_specific: bool,
        translation: str | None = None,
        notes: list[str] | None = None,
        examples: list[dict] | None = None,
        context: str | None = None,
        on_escalate: Callable[[], None] | None = None,
    ) -> RerankResult:
        if not candidates:
            return RerankResult(None, None)
        kwargs = {
            "translation": translation,
            "notes": notes,
            "examples": examples,
            "context": context,
        }
        first: RerankResult | None = None
        try:
            first = await self.first.rerank(term, candidates, drug_specific, **kwargs)
        except Exception as exc:
            logger.warning("Reranker %s failed for %r, escalating: %s", self.first.label, term, exc)
        if first is not None and first.index is not None and (first.confidence or 0) >= self.below:
            return dataclasses.replace(first, route=first.route or "first")
        if (
            first is not None
            and self.from_confidence > 0
            and (first.index is None or (first.confidence or 0) < self.from_confidence)
        ):
            return dataclasses.replace(first, route=first.route or "too_unsure")
        first_vote = {
            "model": self.first.label,
            "index": first.index if first else None,
            "confidence": first.confidence if first else None,
            **({} if first else {"error": "failed"}),
        }
        if on_escalate is not None:
            on_escalate()
        try:
            result = await self.stronger.rerank(term, candidates, drug_specific, **kwargs)
        except Exception:
            if first is None:
                raise
            logger.warning(
                "Escalation to %s failed for %r; keeping the first answer",
                self.stronger.label,
                term,
                exc_info=True,
            )
            return dataclasses.replace(first, route=first.route or "first")
        return dataclasses.replace(
            result,
            route="escalated",
            votes=[*(first.votes if first else []), first_vote],
        )


class CascadeReranker:
    """Several cheaper models pick independently; when they all pick the same candidate that
    pick stands, otherwise the judge decides, seeing their picks.

    In an evaluation on 600 terms two cheap voters agreed on ~87% of
    terms, with ~91% of those right, so the judge only sees the hard ~13%."""

    def __init__(self, voters: list[Reranker], judge: Reranker):
        if len(voters) < 2:
            raise ValueError("A rerank cascade needs at least two voters")
        self.voters = voters
        self.judge = judge
        self.model = f"{'+'.join(v.model for v in voters)} → {judge.model}"
        self.label = f"{'+'.join(v.label for v in voters)} → {judge.label}"

    async def rerank(
        self,
        term: str,
        candidates: list[dict],
        drug_specific: bool,
        translation: str | None = None,
        notes: list[str] | None = None,
        examples: list[dict] | None = None,
        context: str | None = None,
        on_escalate: Callable[[], None] | None = None,
    ) -> RerankResult:
        if not candidates:
            return RerankResult(None, None)
        kwargs = {
            "translation": translation,
            "notes": notes,
            "examples": examples,
            "context": context,
        }
        answers = await asyncio.gather(
            *(v.rerank(term, candidates, drug_specific, **kwargs) for v in self.voters),
            return_exceptions=True,
        )
        votes = []
        for voter, a in zip(self.voters, answers, strict=True):
            if isinstance(a, BaseException):
                logger.warning("Rerank voter %s failed for %r: %s", voter.model, term, a)
                votes.append(
                    {"model": voter.model, "index": None, "confidence": None, "error": str(a)}
                )
            else:
                votes.append({"model": voter.model, "index": a.index, "confidence": a.confidence})
        ok = [a for a in answers if not isinstance(a, BaseException)]
        if len(ok) == len(answers) and len({a.index for a in ok}) == 1:
            first = ok[0]
            confidences = [a.confidence for a in ok if a.confidence is not None]
            return dataclasses.replace(
                first,
                # The most cautious voter's confidence
                confidence=min(confidences) if confidences else None,
                route="agreed",
                votes=votes,
            )
        result = await self.judge.rerank(
            term,
            candidates,
            drug_specific,
            translation=translation,
            notes=[*(notes or []), votes_note(votes, candidates)],
            examples=examples,
            context=context,
        )
        return dataclasses.replace(result, route="judge", votes=votes)


@dataclass
class TranslationResult:
    english: str
    language: str | None = None
    cached: bool = False


class Translator:
    """Renders a source term in English OMOP wording. Results are cached in Redis per model,
    because source vocabularies repeat terms and re-runs should not pay twice."""

    CACHE_TTL = 90 * 24 * 3600

    def __init__(
        self,
        model: str,
        client: instructor.AsyncInstructor | None = None,
        effort: str | None = None,
    ):
        self.model = model
        self.effort = effort or None
        self._client = client

    def _key(
        self,
        term: str,
        context: str | None = None,
        language: str | None = None,
        description: str | None = None,
    ) -> str:
        ident = term.strip() if not context else f"{term.strip()}\n{context}"
        if language:
            ident = f"{ident}\nlanguage={language}"
        if description:
            ident = f"{ident}\ndescription={description}"
        digest = hashlib.sha1(ident.encode()).hexdigest()
        return f"translate:{model_label(self.model, self.effort)}:{digest}"

    async def translate(
        self,
        term: str,
        context: str | None = None,
        language: str | None = None,
        description: str | None = None,
    ) -> TranslationResult:
        """``context``: the source vocabulary (name and description), which can settle what an
        abbreviation means. ``language``: ISO 639-1 code set on the vocabulary; without it the
        model detects the language. ``description``: the term's own description from the
        source list."""
        key = self._key(term, context, language, description)
        try:
            hit = await asyncio.to_thread(get_redis().get, key)
            if hit:
                data = json.loads(hit)
                return TranslationResult(data["english"], data.get("language"), cached=True)
        except Exception:
            logger.debug("Translation cache read failed", exc_info=True)
        client = self._client or get_instructor(self.model)
        response: TranslationResponse = await client.chat.completions.create(
            model=self.model,
            response_model=TranslationResponse,
            messages=[
                {"role": "system", "content": TRANSLATE_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (VOCABULARY_LINE.format(context=context) if context else "")
                    + (SOURCE_LANGUAGE_LINE.format(language=language) if language else "")
                    + f"Term: {term}"
                    + (
                        "\n" + TERM_DESCRIPTION_LINE.format(description=description).strip()
                        if description
                        else ""
                    ),
                },
            ],
            max_retries=get_settings().llm_max_retries,
            **chat_model_settings(self.model, self.effort),
        )
        result = TranslationResult(response.english.strip(), response.language)
        try:
            payload = json.dumps({"english": result.english, "language": result.language})
            await asyncio.to_thread(get_redis().set, key, payload, ex=self.CACHE_TTL)
        except Exception:
            logger.debug("Translation cache write failed", exc_info=True)
        return result


class QueryRewriter:
    """Alternative search queries for a term the first search could not map confidently. Cached
    in Redis per model, term and first candidates."""

    CACHE_TTL = 90 * 24 * 3600

    def __init__(
        self,
        model: str,
        client: instructor.AsyncInstructor | None = None,
        effort: str | None = None,
    ):
        self.model = model
        self.effort = effort or None
        self._client = client

    async def rewrite(
        self,
        term: str,
        *,
        translation: str | None = None,
        context: str | None = None,
        shown: list[str] | None = None,
        description: str | None = None,
    ) -> list[str]:
        """``shown``: names of the candidates the first search found (not to be repeated).
        ``description``: the term's own description from the source list."""
        lines = [VOCABULARY_LINE.format(context=context)] if context else []
        lines.append(f"Source term: {term}\n")
        if description:
            lines.append(TERM_DESCRIPTION_LINE.format(description=description))
        if translation:
            lines.append(TRANSLATION_LINE.format(translation=translation))
        if shown:
            lines.append("Candidates the first search found:\n")
            lines.extend(f"- {name}\n" for name in shown[:15])
        message = "".join(lines)
        digest = hashlib.sha1(message.encode()).hexdigest()
        key = f"rewrite:{model_label(self.model, self.effort)}:{digest}"
        try:
            hit = await asyncio.to_thread(get_redis().get, key)
            if hit:
                return json.loads(hit)
        except Exception:
            logger.debug("Rewrite cache read failed", exc_info=True)
        client = self._client or get_instructor(self.model)
        response: QueryRewriteResponse = await client.chat.completions.create(
            model=self.model,
            response_model=QueryRewriteResponse,
            messages=[
                {"role": "system", "content": REWRITE_SYSTEM_PROMPT},
                {"role": "user", "content": message},
            ],
            max_retries=get_settings().llm_max_retries,
            **chat_model_settings(self.model, self.effort),
        )
        seen = {term.strip().lower(), (translation or "").strip().lower()}
        queries = []
        for q in response.queries:
            q = " ".join(q.split())
            if q and q.lower() not in seen:
                seen.add(q.lower())
                queries.append(q)
        try:
            await asyncio.to_thread(get_redis().set, key, json.dumps(queries), ex=self.CACHE_TTL)
        except Exception:
            logger.debug("Rewrite cache write failed", exc_info=True)
        return queries
