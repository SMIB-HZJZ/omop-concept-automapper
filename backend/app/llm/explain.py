"""AI explanation of a concept from its vocabulary data (cached per concept and model)."""

import asyncio
import json
import logging

from app.core.redis import get_redis
from app.llm.gateway import chat_model_settings, get_async_client
from app.llm.prompts import EXPLAIN_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

CACHE_TTL = 90 * 24 * 3600
# Bump when the prompt changes, so cached explanations are not reused
PROMPT_VERSION = 1


def _line(c: dict) -> str:
    return (
        f"{c['concept_name']} [{c['concept_class_id']} | {c['domain_id']} | {c['vocabulary_id']}"
        f"{' | standard' if c.get('standard_concept') == 'S' else ''}]"
    )


def explain_input(details: dict) -> str:
    """The concept's vocabulary data as the user message."""
    c = details["concept"]
    standard = {"S": "standard", "C": "classification"}.get(
        c.get("standard_concept") or "", "non-standard"
    )
    parts = [
        f"Concept: {c['concept_name']}",
        f"ID {c['concept_id']}, code {c['concept_code']}, {c['vocabulary_id']}, class "
        f"{c['concept_class_id']}, domain {c['domain_id']}, {standard}"
        + (", invalid" if c.get("invalid_reason") else ""),
    ]
    if details["synonyms"]:
        parts.append("Synonyms: " + "; ".join(details["synonyms"][:10]))
    for key, label, count in (
        ("parents", "Parents (more general)", "parent_count"),
        ("children", "Children (more specific)", "child_count"),
    ):
        items = details[key][:12]
        if items:
            more = details[count] - len(items)
            parts.append(
                f"{label}:\n"
                + "\n".join(f"- {_line(x)}" for x in items)
                + (f"\n- ... and {more} more" if more > 0 else "")
            )
    related = details["related"][:25]
    if related:
        parts.append(
            "Other relationships:\n"
            + "\n".join(f"- {r['relationship_id']}: {_line(r)}" for r in related)
        )
    return "\n\n".join(parts)


async def explain_concept(details: dict, model: str, effort: str | None = None) -> tuple[str, bool]:
    """Returns (explanation, cached)."""
    key = f"explain:v{PROMPT_VERSION}:{model}:{effort or ''}:{details['concept']['concept_id']}"
    try:
        hit = await asyncio.to_thread(get_redis().get, key)
        if hit:
            return json.loads(hit)["text"], True
    except Exception:
        logger.debug("Explanation cache read failed", exc_info=True)
    response = await get_async_client().chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": EXPLAIN_SYSTEM_PROMPT},
            {"role": "user", "content": explain_input(details)},
        ],
        **chat_model_settings(model, effort),
    )
    text_ = (response.choices[0].message.content or "").strip()
    if not text_:
        raise RuntimeError("The model returned an empty explanation")
    try:
        await asyncio.to_thread(get_redis().set, key, json.dumps({"text": text_}), ex=CACHE_TTL)
    except Exception:
        logger.debug("Explanation cache write failed", exc_info=True)
    return text_, False
