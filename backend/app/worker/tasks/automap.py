"""Auto-mapping of a whole source vocabulary through a workflow pipeline.

Concepts are processed concurrently (AUTOMAP_CONCURRENCY). The pipeline only decides; this task
persists accepted results either as pending suggestions (review mode) or directly as mappings
(commit mode, the Streamlit app's behaviour).
"""

import asyncio
import logging

from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import session_scope
from app.llm.gateway import aclose_async_client
from app.services import mapping
from app.services.settings_store import get_effective_config
from app.worker.base import TrackedTask
from app.worker.celery_app import celery_app
from app.worker.progress import ProgressReporter, TaskCancelled, TaskFailed
from app.workflows.builder import get_pipeline
from app.workflows.context import NodeContext
from app.workflows.service import build_context, default_pipeline, initial_state, run_pipeline
from app.workflows.state import DEFAULT_CONFIDENCE_THRESHOLD

logger = logging.getLogger(__name__)


def persist_outcome(
    state: dict,
    *,
    mode: str,
    pipeline: str,
    domains: list[str],
    task_id: str | None,
    user_id: int | None,
) -> str:
    """Store an accepted decision. Returns 'mapped', 'queued' or 'skipped'."""
    if state.get("outcome") != "accepted" or not state.get("selected"):
        return "skipped"
    selected = state["selected"]
    method = state.get("method") or "auto_standard"
    with session_scope() as session:
        if mode == "commit":
            written = mapping.map_concepts(
                session,
                state["source_id"],
                [selected["concept_id"]],
                method=method,
                user_id=user_id,
                confidence=state.get("confidence"),
                target_domains=domains,
                pipeline=pipeline,
                task_id=task_id,
                rationale=state.get("rationale"),
                commit=False,
                only_if_unmapped=True,
            )
            return "mapped" if written else "skipped"
        # Not when the concept was mapped (e.g. by hand) while this run was going
        inserted = session.execute(
            text(
                """
                INSERT INTO mapping_suggestion
                    (source_id, concept_id, confidence, method, pipeline, target_domains, rationale, task_id)
                SELECT :s, :c, :conf, :method, :pipeline, :domains, :rationale, :task
                WHERE NOT EXISTS (SELECT 1 FROM source_standard_map WHERE source_id = :s)
                ON CONFLICT (source_id) WHERE status = 'pending' DO NOTHING
                """
            ),
            {
                "s": state["source_id"],
                "c": selected["concept_id"],
                "conf": int(state.get("confidence") or 0),
                "method": method,
                "pipeline": pipeline,
                "domains": domains,
                "rationale": state.get("rationale"),
                "task": task_id,
            },
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
) -> dict:
    compiled = compiled or get_pipeline(pipeline)
    counts = {"processed": 0, "mapped": 0, "queued": 0, "skipped": 0, "errors": 0}
    outcomes: dict[str, int] = {}
    semaphore = asyncio.Semaphore(max(1, concurrency))
    cancelled = asyncio.Event()
    total = len(concepts)

    async def handle(concept: dict) -> None:
        async with semaphore:
            if cancelled.is_set():
                return
            try:
                state = await run_pipeline(
                    initial_state(
                        source_id=concept["source_id"],
                        source_value=concept["source_value"],
                        source_name=concept["source_concept_name"],
                        domains=domains,
                        drug_specific=drug_specific,
                        threshold=threshold,
                        drug_structure=concept.get("drug_structure"),
                    ),
                    ctx,
                    compiled=compiled,
                )
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
                counts["processed"] += 1
                if progress:
                    message = (
                        f"{counts['processed']:,}/{total:,} processed · "
                        f"{counts['mapped'] + counts['queued']:,} "
                        f"{'mapped' if mode == 'commit' else 'queued for review'}"
                    )
                    # DB and Redis I/O: keep it off the event loop
                    if await asyncio.to_thread(_report, progress, counts["processed"], message):
                        cancelled.set()

    await asyncio.gather(*(handle(c) for c in concepts))
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
) -> dict:
    pipeline = pipeline or default_pipeline(drug_specific)
    compiled = get_pipeline(pipeline)  # resolved once; also fails fast on an invalid pipeline
    with session_scope() as session:
        cfg = get_effective_config(session)
        concepts = mapping.get_unmapped_concepts_for_automap(session, vocabulary_id, limit)
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
