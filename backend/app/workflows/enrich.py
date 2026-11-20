"""Concept context (parents, defining attributes, synonyms) for the candidates an AI step sees."""

import asyncio

from app.workflows.context import NodeContext


async def with_context(ctx: NodeContext, candidates: list[dict], top: int) -> list[dict]:
    """A copy of ``candidates`` whose first ``top`` carry a ``context``. Best effort: without a
    lookup, or when it fails, the candidates go as they are."""
    if top <= 0 or not candidates or ctx.get_concept_context is None:
        return candidates
    ids = [c["concept_id"] for c in candidates[:top]]
    try:
        found = await asyncio.to_thread(ctx.get_concept_context, ids)
    except Exception:
        ctx.logger.warning("Concept context lookup failed", exc_info=True)
        return candidates
    return [
        {**c, "context": found[c["concept_id"]]} if i < top and c["concept_id"] in found else c
        for i, c in enumerate(candidates)
    ]
