"""Measure mapping quality against known mappings: pipelines (e.g. with/without translation) and
LLM models compared on the same sample, with a paired significance test.

Ground truth is the existing mappings of a source vocabulary. Mappings written by an unreviewed
auto-map run (commit mode) are excluded by default, so the pipeline is never graded against its
own output. Nothing is written to the database; results go to a JSON summary and a per-item CSV.
"""

import asyncio
import csv
import json
import logging
import math
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from sqlalchemy import text
from sqlmodel import Session

from app.llm.gateway import aclose_async_client
from app.services.settings_store import EffectiveConfig
from app.workflows.builder import get_pipeline
from app.workflows.context import NodeContext
from app.workflows.service import initial_state, run_pipeline

logger = logging.getLogger(__name__)


@dataclass
class GoldItem:
    source_id: int
    source_value: str
    source_name: str
    gold: set[int]
    gold_domains: set[str]
    drug_structure: dict | None = None


@dataclass
class ItemResult:
    variant: str
    source_id: int
    correct: bool = False  # the pipeline's pick is one of the gold concepts
    accepted: bool = False  # the pick passed the confidence threshold
    in_candidates: bool = False  # a gold concept was retrieved (retrieval recall)
    gold_rank: int | None = None  # rank of the best gold concept among the candidates
    selected: int | None = None
    confidence: int | None = None
    outcome: str = ""
    translated_query: str | None = None
    seconds: float = 0.0


@dataclass
class VariantSummary:
    variant: str
    pipeline: str
    llm_model: str
    n: int = 0
    retrieval_recall: float = 0.0
    mean_reciprocal_rank: float = 0.0
    top1_accuracy: float = 0.0
    accepted: int = 0
    accepted_precision: float | None = None
    coverage: float = 0.0
    errors: int = 0
    no_candidates: int = 0
    seconds_per_concept: float = 0.0
    outcomes: dict[str, int] = field(default_factory=dict)


def load_gold(
    session: Session,
    vocabulary_id: int,
    sample: int,
    seed: int = 42,
    include_unreviewed_auto: bool = False,
) -> list[GoldItem]:
    """A reproducible random sample of mapped source concepts with their gold concept ids."""
    rows = session.execute(
        text(
            """
            WITH gold AS (
                SELECT m.source_id, m.concept_id, c.domain_id
                FROM source_standard_map m
                JOIN source_concepts sc ON sc.source_id = m.source_id
                JOIN concept c ON c.concept_id = m.concept_id
                WHERE sc.source_vocabulary_id = :v
                  AND (
                      :include_auto
                      -- written by an auto-map run in commit mode and never reviewed
                      OR NOT EXISTS (
                          SELECT 1 FROM auto_mapping_audit a
                          WHERE a.source_id = m.source_id AND a.concept_id = m.concept_id
                            AND a.mapping_method LIKE 'auto%'
                            AND NOT EXISTS (
                                SELECT 1 FROM mapping_suggestion s
                                WHERE s.source_id = m.source_id AND s.concept_id = m.concept_id
                                  AND s.status = 'accepted'
                            )
                      )
                  )
            )
            SELECT sc.source_id, sc.source_value, sc.source_concept_name, sc.drug_structure,
                   array_agg(DISTINCT g.concept_id) AS gold,
                   array_agg(DISTINCT g.domain_id) AS domains
            FROM gold g JOIN source_concepts sc ON sc.source_id = g.source_id
            GROUP BY sc.source_id, sc.source_value, sc.source_concept_name, sc.drug_structure
            ORDER BY md5(sc.source_id::text || :seed)
            LIMIT :n
            """
        ),
        {
            "v": vocabulary_id,
            "n": sample,
            "seed": str(seed),
            "include_auto": include_unreviewed_auto,
        },
    ).mappings()
    return [
        GoldItem(
            source_id=r["source_id"],
            source_value=r["source_value"] or "",
            source_name=r["source_concept_name"],
            gold=set(r["gold"]),
            gold_domains=set(r["domains"]),
            drug_structure=r["drug_structure"],
        )
        for r in rows
    ]


async def _evaluate_variant(
    variant: str,
    pipeline: str,
    items: list[GoldItem],
    ctx: NodeContext,
    *,
    domains: list[str],
    drug_specific: bool,
    threshold: int,
    concurrency: int,
) -> list[ItemResult]:
    compiled = get_pipeline(pipeline)
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def one(item: GoldItem) -> ItemResult:
        async with semaphore:
            result = ItemResult(variant=variant, source_id=item.source_id)
            started = time.monotonic()
            try:
                state = await run_pipeline(
                    initial_state(
                        source_id=item.source_id,
                        source_value=item.source_value,
                        source_name=item.source_name,
                        domains=domains,
                        drug_specific=drug_specific,
                        threshold=threshold,
                        drug_structure=item.drug_structure,
                    ),
                    ctx,
                    compiled=compiled,
                )
            except Exception as exc:
                logger.warning("Evaluation of %s failed: %s", item.source_id, exc)
                result.outcome = "error"
                return result
            finally:
                result.seconds = time.monotonic() - started
            candidates = [c["concept_id"] for c in state.get("candidates") or []]
            ranks = [i + 1 for i, cid in enumerate(candidates) if cid in item.gold]
            selected = (state.get("selected") or {}).get("concept_id")
            result.in_candidates = bool(ranks)
            result.gold_rank = ranks[0] if ranks else None
            result.selected = selected
            result.confidence = state.get("confidence")
            result.correct = selected is not None and selected in item.gold
            result.accepted = state.get("outcome") == "accepted"
            result.outcome = state.get("outcome", "")
            result.translated_query = state.get("translated_query")
            return result

    return await asyncio.gather(*(one(item) for item in items))


def summarise(
    variant: str, pipeline: str, llm_model: str, results: list[ItemResult]
) -> VariantSummary:
    n = len(results)
    summary = VariantSummary(variant=variant, pipeline=pipeline, llm_model=llm_model, n=n)
    if not n:
        return summary
    accepted = [r for r in results if r.accepted]
    summary.retrieval_recall = sum(r.in_candidates for r in results) / n
    summary.mean_reciprocal_rank = sum(1 / r.gold_rank for r in results if r.gold_rank) / n
    summary.top1_accuracy = sum(r.correct for r in results) / n
    summary.accepted = len(accepted)
    summary.accepted_precision = (
        sum(r.correct for r in accepted) / len(accepted) if accepted else None
    )
    summary.coverage = len(accepted) / n
    summary.errors = sum(r.outcome == "error" for r in results)
    summary.no_candidates = sum(r.outcome == "no_candidates" for r in results)
    summary.seconds_per_concept = sum(r.seconds for r in results) / n
    for r in results:
        summary.outcomes[r.outcome] = summary.outcomes.get(r.outcome, 0) + 1
    return summary


def sign_test(a: list[ItemResult], b: list[ItemResult], metric: str = "correct") -> dict:
    """Paired comparison on the same concepts (exact two-sided sign/McNemar test).

    ``only_a``: concepts variant A gets right and B gets wrong; ``only_b``: the reverse."""
    by_id = {r.source_id: r for r in b}
    only_a = only_b = 0
    for ra in a:
        rb = by_id.get(ra.source_id)
        if rb is None:
            continue
        va, vb = getattr(ra, metric), getattr(rb, metric)
        only_a += va and not vb
        only_b += vb and not va
    discordant = only_a + only_b
    if discordant == 0:
        p = 1.0
    else:
        k = min(only_a, only_b)
        p = min(1.0, 2 * sum(math.comb(discordant, i) for i in range(k + 1)) / 2**discordant)
    return {"metric": metric, "only_a": only_a, "only_b": only_b, "p_value": round(p, 4)}


@dataclass
class Variant:
    pipeline: str
    llm_model: str

    @property
    def name(self) -> str:
        return f"{self.pipeline} @ {self.llm_model}"


async def evaluate(
    items: list[GoldItem],
    variants: list[Variant],
    make_context,
    *,
    domains: list[str],
    drug_specific: bool,
    threshold: int,
    concurrency: int,
) -> tuple[list[VariantSummary], dict[str, list[ItemResult]], list[dict]]:
    """Run every variant on the same items. ``make_context(llm_model)`` builds the node context."""
    per_variant: dict[str, list[ItemResult]] = {}
    summaries = []
    try:
        for v in variants:
            logger.info("Evaluating %s on %d concepts", v.name, len(items))
            results = await _evaluate_variant(
                v.name,
                v.pipeline,
                items,
                make_context(v.llm_model),
                domains=domains,
                drug_specific=drug_specific,
                threshold=threshold,
                concurrency=concurrency,
            )
            per_variant[v.name] = results
            summaries.append(summarise(v.name, v.pipeline, v.llm_model, results))
    finally:
        await aclose_async_client()
    baseline = variants[0].name
    comparisons = [
        {
            "a": baseline,
            "b": v.name,
            **{
                m: sign_test(per_variant[baseline], per_variant[v.name], m)
                for m in ("correct", "in_candidates")
            },
        }
        for v in variants[1:]
    ]
    return summaries, per_variant, comparisons


def write_report(
    out_dir: Path,
    summaries: list[VariantSummary],
    per_variant: dict[str, list[ItemResult]],
    comparisons: list[dict],
    items: list[GoldItem],
    settings: dict,
) -> tuple[Path, Path]:
    """``summary.json`` (metrics only) and ``items.csv`` (per concept, includes source terms)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = out_dir / "summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "settings": settings,
                "variants": [asdict(s) for s in summaries],
                "comparisons": comparisons,
            },
            indent=2,
        )
        + "\n"
    )
    by_id = {i.source_id: i for i in items}
    items_path = out_dir / "items.csv"
    with items_path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "variant",
                "source_id",
                "source_name",
                "translated_query",
                "gold",
                "selected",
                "confidence",
                "correct",
                "accepted",
                "gold_rank",
                "outcome",
            ]
        )
        for variant, results in per_variant.items():
            for r in results:
                item = by_id[r.source_id]
                writer.writerow(
                    [
                        variant,
                        r.source_id,
                        item.source_name,
                        r.translated_query or "",
                        ";".join(map(str, sorted(item.gold))),
                        r.selected or "",
                        r.confidence or "",
                        int(r.correct),
                        int(r.accepted),
                        r.gold_rank or "",
                        r.outcome,
                    ]
                )
    return summary_path, items_path


def format_table(summaries: list[VariantSummary], comparisons: list[dict]) -> str:
    def pct(x: float | None) -> str:
        return "—" if x is None else f"{x * 100:5.1f}%"

    lines = [
        "| variant | n | retrieval recall | MRR | top-1 accuracy | accepted | precision of accepted | errors |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for s in summaries:
        lines.append(
            f"| {s.variant} | {s.n} | {pct(s.retrieval_recall)} | {s.mean_reciprocal_rank:.3f} | "
            f"{pct(s.top1_accuracy)} | {s.accepted} ({pct(s.coverage)}) | "
            f"{pct(s.accepted_precision)} | {s.errors} |"
        )
    for c in comparisons:
        corr, ret = c["correct"], c["in_candidates"]
        lines.append(
            f"\n{c['b']} vs {c['a']}: top-1 better on {corr['only_b']}, worse on {corr['only_a']} "
            f"(p={corr['p_value']}); retrieval better on {ret['only_b']}, worse on {ret['only_a']} "
            f"(p={ret['p_value']})"
        )
    return "\n".join(lines)


def build_variants(
    pipelines: list[str], llm_models: list[str], cfg: EffectiveConfig
) -> list[Variant]:
    models = llm_models or [cfg.llm_model]
    return [Variant(p, m) for m in models for p in pipelines]
