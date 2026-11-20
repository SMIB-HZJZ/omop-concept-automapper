"""Deterministic steps before any search: earlier reviewed mappings, source codes, exact names.

Each is a router: ``matched`` ends the pipeline (via the confidence gate), ``next`` continues.
Ambiguous hits are passed on as the first candidates for the reranker.
"""

import asyncio
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.workflows.context import NodeContext
from app.workflows.registry import router
from app.workflows.state import AutomapState


class TextMatchParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confidence: int = Field(default=10, ge=1, le=10)


def _in_domains(state: AutomapState, concepts: list[dict]) -> list[dict]:
    """With domain_mode 'restrict', drop concepts outside the chosen domains."""
    domains = state.get("domains") or []
    if (state.get("domain_mode") or "restrict") != "restrict" or not domains:
        return concepts
    return [c for c in concepts if c.get("domain_id") in domains]


def _outcome(state: AutomapState, match, params: TextMatchParams, method: str, label: str):
    concepts = _in_domains(state, match.concepts)
    if not concepts:
        reason = "outside the chosen domains" if match.concepts else "no match"
        return "next", {"_summary": f"{label}: {reason}"}
    names = " + ".join(repr(c["concept_name"]) for c in concepts)
    if match.ambiguous:
        return "next", {
            "structured_candidates": [*(state.get("structured_candidates") or []), *concepts][:30],
            "_summary": f"{label}: {len(concepts)} different targets, left to the reranker",
        }
    return "matched", {
        "selected": concepts[0],
        "selected_concepts": concepts if len(concepts) > 1 else [],
        "confidence": params.confidence,
        "rationale": f"{label} → {names}",
        "method": method,
        "match_type": "exact",
        "outcome": "selected",
        "_summary": f"{label} → {names}",
    }


@router(
    "match_previous",
    routes={"matched", "next"},
    params=TextMatchParams,
    description="Reuse the reviewed mapping of another source concept with the same name",
)
async def match_previous(state: AutomapState, ctx: NodeContext, params: TextMatchParams):
    if ctx.lexical is None:
        return "next", {"_summary": "skipped (no database)"}
    match = await asyncio.to_thread(
        ctx.lexical.match_previous, state.get("source_name", ""), state.get("source_id")
    )
    return _outcome(state, match, params, "auto_previous", "same name mapped before")


@router(
    "match_code",
    routes={"matched", "next"},
    params=TextMatchParams,
    description="Look up the source code in the vocabulary's code systems (e.g. ICD10) and follow Maps to",
)
async def match_code(state: AutomapState, ctx: NodeContext, params: TextMatchParams):
    vocabularies = state.get("code_vocabularies") or []
    if ctx.lexical is None or not vocabularies:
        return "next", {"_summary": "skipped (no code systems set for this vocabulary)"}
    match = await asyncio.to_thread(
        ctx.lexical.match_code, state.get("source_value", ""), vocabularies
    )
    code = state.get("source_value", "")
    return _outcome(state, match, params, "auto_code", f"code {code} ({', '.join(vocabularies)})")


class NameMatchParams(TextMatchParams):
    confidence: int = Field(default=9, ge=1, le=10)
    # original: the source term (before translating, so exact hits need no translation);
    # english: the translation (its hits only become the reranker's first candidates);
    # both: original, then English
    term: Literal["original", "english", "both"] = "both"


@router(
    "match_name",
    routes={"matched", "next"},
    params=NameMatchParams,
    description="Exact name or synonym match (case, accents and punctuation ignored), then Maps to; "
    "a match of the English translation only goes to the reranker as the first candidates",
)
async def match_name(state: AutomapState, ctx: NodeContext, params: NameMatchParams):
    if ctx.lexical is None:
        return "next", {"_summary": "skipped (no database)"}
    if params.term != "english":
        match = await asyncio.to_thread(ctx.lexical.match_name, [state.get("source_name", "")])
        if _in_domains(state, match.concepts) or params.term == "original":
            return _outcome(state, match, params, "auto_exact_name", "exact name/synonym")
    translated = state.get("translated_query")
    if not translated:
        return "next", {"_summary": "exact name/synonym: no match (no English rendering)"}
    # A machine translation can be wrong, so an exact hit on it is a strong candidate for the
    # reranker, not an accepted mapping
    hit = await asyncio.to_thread(ctx.lexical.match_name, [translated])
    concepts = _in_domains(state, hit.concepts)
    if not concepts:
        return "next", {"_summary": "exact name/synonym of the English rendering: no match"}
    names = " + ".join(repr(c["concept_name"]) for c in concepts)
    return "next", {
        "structured_candidates": [*(state.get("structured_candidates") or []), *concepts][:30],
        "_summary": f"English {translated!r} matches {names} exactly; left to the reranker",
    }
