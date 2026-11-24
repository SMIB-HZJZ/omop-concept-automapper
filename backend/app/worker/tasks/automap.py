"""Auto-mapping of a whole source vocabulary through a workflow pipeline.

Concepts are processed concurrently (AUTOMAP_CONCURRENCY). The pipeline only decides; this task
persists accepted results either as pending suggestions (review mode) or directly as mappings
(commit mode, the Streamlit app's behaviour).
"""

import asyncio
import json
import logging
from collections import Counter

from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import session_scope
from app.llm import prompts
from app.llm.gateway import aclose_async_client
from app.models import SourceVocabulary
from app.services import mapping
from app.services.settings_store import get_effective_config
from app.worker.base import TrackedTask
from app.worker.celery_app import celery_app
from app.worker.progress import ProgressReporter, TaskCancelled, TaskFailed
from app.workflows.builder import get_pipeline
from app.workflows.context import NodeContext
from app.workflows.service import build_context, default_pipeline, initial_state, run_pipeline
from app.workflows.state import DEFAULT_CONFIDENCE_THRESHOLD
from app.workflows.steps import PART2_METHODS, decision_path, step_label

logger = logging.getLogger(__name__)


_BELOW_THRESHOLD_SQL = text(
    """
    INSERT INTO mapping_suggestion
        (source_id, concept_id, extra_concept_ids, confidence, method, pipeline,
         target_domains, rationale, task_id, match_type, decision_path, status)
    SELECT :s, :c, CAST(:extra AS integer[]), :conf, :method, :pipeline, :domains,
           :rationale, :task, :match_type, :path, 'below_threshold'
    WHERE NOT EXISTS (SELECT 1 FROM source_standard_map WHERE source_id = :s)
    ON CONFLICT (source_id) WHERE status = 'below_threshold' DO UPDATE SET
        concept_id = EXCLUDED.concept_id, extra_concept_ids = EXCLUDED.extra_concept_ids,
        confidence = EXCLUDED.confidence, method = EXCLUDED.method,
        pipeline = EXCLUDED.pipeline, target_domains = EXCLUDED.target_domains,
        rationale = EXCLUDED.rationale, task_id = EXCLUDED.task_id,
        match_type = EXCLUDED.match_type, decision_path = EXCLUDED.decision_path,
        created_at = now()
    """
)


def persist_outcome(
    state: dict,
    *,
    mode: str,
    pipeline: str,
    domains: list[str],
    task_id: str | None,
    user_id: int | None,
) -> str:
    """Store the decision. Returns 'mapped', 'queued', 'below_threshold' or 'skipped'.

    A selection under the confidence threshold is kept (one row per term, the latest run wins)
    so the Map list can show it; it never enters the review queue."""
    selected = state.get("selected")
    accepted = state.get("outcome") == "accepted" and selected
    concept_ids = [c["concept_id"] for c in state.get("selected_concepts") or []] or (
        [selected["concept_id"]] if selected else []
    )
    method = state.get("method") or "auto_standard"
    params = {
        "s": state["source_id"],
        "c": concept_ids[0] if concept_ids else None,
        "extra": concept_ids[1:],
        "conf": int(state.get("confidence") or 0),
        "method": method,
        "pipeline": pipeline,
        "domains": domains,
        "rationale": state.get("rationale"),
        "task": task_id,
        "match_type": state.get("match_type"),
        "path": decision_path(state),
    }
    with session_scope() as session:
        # The term before any suggestion or mapping row (see mapping.lock_terms)
        mapping.lock_terms(session, [state["source_id"]])
        if not accepted:
            if selected and state.get("confidence") is not None:
                stored = session.execute(_BELOW_THRESHOLD_SQL, params).rowcount
                return "below_threshold" if stored else "skipped"
            # Nothing usable this time: an older below-threshold result is no longer the latest
            session.execute(
                text(
                    "DELETE FROM mapping_suggestion "
                    "WHERE source_id = :s AND status = 'below_threshold'"
                ),
                {"s": state["source_id"]},
            )
            return "skipped"
        session.execute(
            text(
                "DELETE FROM mapping_suggestion WHERE source_id = :s AND status = 'below_threshold'"
            ),
            {"s": state["source_id"]},
        )
        # Picks made for unsure terms (rewritten search, agent, final decision) always go to
        # Review: a confidence reached after several failed attempts is not yet known to be as
        # reliable as a first pick's
        if mode == "commit" and method not in PART2_METHODS:
            written = mapping.map_concepts(
                session,
                state["source_id"],
                concept_ids,
                method=method,
                user_id=user_id,
                confidence=state.get("confidence"),
                target_domains=domains,
                pipeline=pipeline,
                task_id=task_id,
                rationale=state.get("rationale"),
                match_type=state.get("match_type"),
                commit=False,
                only_if_unmapped=True,
            )
            return "mapped" if written else "skipped"
        # Not when the concept was mapped (e.g. by hand) while this run was going
        inserted = session.execute(
            text(
                """
                INSERT INTO mapping_suggestion
                    (source_id, concept_id, extra_concept_ids, confidence, method, pipeline,
                     target_domains, rationale, task_id, match_type, decision_path)
                SELECT :s, :c, CAST(:extra AS integer[]), :conf, :method, :pipeline, :domains,
                       :rationale, :task, :match_type, :path
                WHERE NOT EXISTS (SELECT 1 FROM source_standard_map WHERE source_id = :s)
                ON CONFLICT (source_id) WHERE status = 'pending' DO NOTHING
                """
            ),
            params,
        ).rowcount
        return "queued" if inserted else "skipped"


def _report(progress: ProgressReporter, current: int, message: str) -> bool:
    """Write (throttled) progress and return whether the user asked to cancel."""
    progress.update(current=current, message=message)
    return progress.is_cancelled()


async def automap_concepts(
    concepts: list[dict],
    ctx: NodeContext,
    *,
    domains: list[str],
    threshold: int,
    drug_specific: bool,
    mode: str,
    pipeline: str,
    progress: ProgressReporter | None,
    task_id: str | None,
    user_id: int | None,
    concurrency: int,
    compiled: tuple | None = None,
    domain_mode: str = "prefer",
    code_vocabularies: list[str] | None = None,
    vocabulary_context: str | None = None,
    source_language: str | None = None,
    vocabularies: list[str] | None = None,
) -> dict:
    compiled = compiled or get_pipeline(pipeline)
    counts = {
        "processed": 0,
        "mapped": 0,
        "queued": 0,
        "below_threshold": 0,
        "skipped": 0,
        "errors": 0,
        "reused": 0,
    }
    # Source lists repeat terms: decide each distinct (name, code, structure) once per run
    decisions: dict[tuple, asyncio.Future] = {}

    def dedupe_key(concept: dict) -> tuple:
        return (
            " ".join((concept["source_concept_name"] or "").lower().split()),
            (concept["source_value"] or "").strip().upper() if code_vocabularies else "",
            json.dumps(concept.get("drug_structure"), sort_keys=True),
        )

    async def decide(concept: dict) -> dict:
        return await run_pipeline(
            initial_state(
                source_id=concept["source_id"],
                source_value=concept["source_value"],
                source_name=concept["source_concept_name"],
                domains=domains,
                vocabularies=vocabularies,
                drug_specific=drug_specific,
                threshold=threshold,
                drug_structure=concept.get("drug_structure"),
                domain_mode=domain_mode,
                code_vocabularies=code_vocabularies,
                vocabulary_context=vocabulary_context,
                source_language=source_language,
            ),
            ctx,
            compiled=compiled,
        )

    outcomes: dict[str, int] = {}
    # Live view: the step each term in progress is at
    current: dict[int, str] = {}

    def on_step(state, node_id, uses, detail):
        label = step_label(node_id, uses, None)  # details (agent queries) are too long here
        if label and state.get("source_id") is not None:
            current[state["source_id"]] = label

    ctx.on_step = on_step
    semaphore = asyncio.Semaphore(max(1, concurrency))
    cancelled = asyncio.Event()
    total = len(concepts)

    async def handle(concept: dict) -> None:
        async with semaphore:
            if cancelled.is_set():
                return
            try:
                key = dedupe_key(concept)
                future = decisions.get(key)
                if future is None:
                    future = decisions[key] = asyncio.ensure_future(decide(concept))
                else:
                    counts["reused"] += 1
                state = {**(await future), "source_id": concept["source_id"]}
                outcomes[state.get("outcome", "unknown")] = (
                    outcomes.get(state.get("outcome", "unknown"), 0) + 1
                )
                if state.get("outcome") == "error":
                    counts["errors"] += 1
                result = await asyncio.to_thread(
                    persist_outcome,
                    state,
                    mode=mode,
                    pipeline=pipeline,
                    domains=domains,
                    task_id=task_id,
                    user_id=user_id,
                )
                counts[result] += 1
            except Exception:
                logger.exception("Auto-mapping failed for source concept %s", concept["source_id"])
                counts["errors"] += 1
            finally:
                current.pop(concept["source_id"], None)
                counts["processed"] += 1
                if progress:
                    message = status_line()
                    # DB and Redis I/O: keep it off the event loop
                    if await asyncio.to_thread(_report, progress, counts["processed"], message):
                        cancelled.set()

    def status_line() -> str:
        line = (
            f"{counts['processed']:,}/{total:,} processed · "
            f"{counts['mapped'] + counts['queued']:,} "
            f"{'mapped' if mode == 'commit' else 'queued for review'}"
        )
        steps = Counter(current.values())
        if steps:
            line += " · now: " + ", ".join(f"{n} {label}" for label, n in steps.most_common())
        return line

    async def tick() -> None:
        # Long steps (agent retries) finish no term for a while; keep the line current
        while True:
            await asyncio.sleep(2)
            if progress and current:
                await asyncio.to_thread(
                    progress.update, current=counts["processed"], message=status_line()
                )

    ticker = asyncio.create_task(tick())
    try:
        await asyncio.gather(*(handle(c) for c in concepts))
    finally:
        ticker.cancel()
    return {**counts, "outcomes": outcomes, "cancelled": cancelled.is_set()}


@celery_app.task(bind=True, base=TrackedTask, name="automap.run")
def run_automap(
    self,
    vocabulary_id: int,
    domains: list[str],
    threshold: int = DEFAULT_CONFIDENCE_THRESHOLD,
    drug_specific: bool = False,
    mode: str = "review",
    pipeline: str | None = None,
    limit: int | None = None,
    user_id: int | None = None,
    domain_mode: str = "prefer",
    vocabularies: list[str] | None = None,
) -> dict:
    pipeline = pipeline or default_pipeline(drug_specific)
    compiled = get_pipeline(pipeline)  # resolved once; also fails fast on an invalid pipeline
    with session_scope() as session:
        cfg = get_effective_config(session)
        concepts = mapping.get_unmapped_concepts_for_automap(session, vocabulary_id, limit)
        vocabulary = session.get(SourceVocabulary, vocabulary_id)
        code_vocabularies = list(vocabulary.code_vocabularies or []) if vocabulary else []
        source_language = vocabulary.source_language if vocabulary else None
        context = (
            prompts.vocabulary_context(vocabulary.name, vocabulary.description)
            if vocabulary
            else None
        )
    self.progress.phase(
        f"Auto-mapping {len(concepts):,} concepts with '{pipeline}'", total=len(concepts)
    )
    if not concepts:
        return {
            "vocabulary_id": vocabulary_id,
            "total_concepts": 0,
            "processed": 0,
            "mapped": 0,
            "queued": 0,
        }

    async def run() -> dict:
        try:
            return await automap_concepts(
                concepts,
                build_context(cfg),
                domains=domains,
                threshold=threshold,
                drug_specific=drug_specific,
                mode=mode,
                pipeline=pipeline,
                progress=self.progress,
                task_id=self.request.id,
                user_id=user_id,
                concurrency=get_settings().automap_concurrency,
                compiled=compiled,
                domain_mode=domain_mode,
                code_vocabularies=code_vocabularies,
                vocabulary_context=context,
                source_language=source_language,
                vocabularies=vocabularies,
            )
        finally:
            await aclose_async_client()

    result = asyncio.run(run())
    mapping.invalidate_vocabulary(vocabulary_id)
    summary = {
        "vocabulary_id": vocabulary_id,
        "total_concepts": len(concepts),
        "mode": mode,
        "pipeline": pipeline,
        "mapping_method": "auto_drug" if drug_specific else "auto_standard",
        "confidence_threshold": threshold,
        "target_domains": domains,
        "llm_model": cfg.llm_model,
        **result,
    }
    if result["cancelled"]:
        done = result["mapped"] + result["queued"]
        raise TaskCancelled(
            f"Cancelled after {result['processed']:,} of {len(concepts):,} concepts ({done:,} saved)"
        )
    if result["processed"] and result["errors"] == result["processed"]:
        # Typically the LLM gateway or provider is down or misconfigured
        raise TaskFailed(
            f"All {result['errors']:,} concepts failed; check the LLM gateway and model settings",
            summary,
        )
    return summary
