"""Run a synthetic drug list through the real automap_drug pipeline and score the result.

Uses the configured LLMs (gateway, model, escalation) and the loaded vocabulary, so it costs LLM
calls; parsed names, dose form choices and translations are cached in Redis, a re-run is cheaper.

Run inside the dev stack:

    docker compose -f docker-compose.dev.yaml exec -T backend \
        python scripts/eval_drug_pipeline.py [--cases eval/drug_synthetic_cases.csv] [--out FILE]

Cases (CSV): ``source_name``, optional drug columns as in a source import (``ingredient``,
``amount_value``, ..., ``quantity_unit``), and ``expected``: acceptable answers separated by ``|``,
each a concept name, or several joined by `` + `` for a combined mapping, or ``NONE`` when nothing
should be accepted (not a drug). A case is correct when the final selection is one of them.
"""

import argparse
import asyncio
import csv
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402
from sqlmodel import Session  # noqa: E402

from app.core.db import get_engine  # noqa: E402
from app.services.settings_store import get_effective_config  # noqa: E402
from app.services.source_import import parse_drug_structure  # noqa: E402
from app.workflows.builder import get_pipeline  # noqa: E402
from app.workflows.service import (  # noqa: E402
    build_context,
    default_pipeline,
    initial_state,
    run_pipeline,
)

DRUG_COLUMNS = (
    "ingredient",
    "amount_value",
    "amount_unit",
    "numerator_value",
    "numerator_unit",
    "denominator_value",
    "denominator_unit",
    "dose_form",
    "brand_name",
    "quantity_value",
    "quantity_unit",
)


def _norm(name: str) -> str:
    return " ".join(name.lower().split())


def check_expected(cases: list[dict]) -> list[str]:
    """Expected concept names that are not standard RxNorm/RxNorm Extension concepts."""
    names = {
        _norm(part)
        for c in cases
        for alt in c["expected"].split("|")
        for part in alt.split(" + ")
        if alt.strip() != "NONE"
    }
    with Session(get_engine()) as session:
        found = set(
            session.execute(
                text(
                    """
                    SELECT lower(concept_name) FROM concept
                    WHERE standard_concept = 'S' AND vocabulary_id IN ('RxNorm', 'RxNorm Extension')
                      AND lower(concept_name) = ANY(:names)
                    """
                ),
                {"names": sorted(names)},
            ).scalars()
        )
    return sorted(names - {_norm(n) for n in found})


def score(case: dict, result: dict) -> dict:
    selected = result.get("selected_concepts") or (
        [result["selected"]] if result.get("selected") else []
    )
    picked = sorted(_norm(c["concept_name"]) for c in selected)
    accepted = result.get("outcome") == "accepted"
    alternatives = [a.strip() for a in case["expected"].split("|")]
    if alternatives == ["NONE"]:
        correct = not accepted
    else:
        wanted = [sorted(_norm(p) for p in a.split(" + ")) for a in alternatives]
        correct = picked in wanted
    drug_step = next((t for t in result.get("trace", []) if t["node"] == "match_drug"), {})
    return {
        "picked": " + ".join(c["concept_name"] for c in selected),
        "correct": correct,
        "accepted": accepted,
        "confidence": result.get("confidence"),
        "method": result.get("method") or "",
        "approximate": "approximate" in drug_step.get("summary", ""),
        "match_drug": drug_step.get("summary", ""),
        "path": " → ".join(t["node"] for t in result.get("trace", [])),
        "rationale": (result.get("rationale") or "")[:300],
    }


async def evaluate(cases: list[dict], threshold: int, concurrency: int) -> list[dict]:
    with Session(get_engine()) as session:
        cfg = get_effective_config(session)
    ctx = build_context(cfg)
    pipeline = default_pipeline(True)
    compiled = get_pipeline(pipeline)
    print(f"pipeline {pipeline}, model {cfg.llm_model}, {len(cases)} cases", flush=True)
    gate = asyncio.Semaphore(concurrency)
    rows: list[dict] = []

    async def one(i: int, case: dict) -> None:
        columns = {c: case.get(c, "") for c in DRUG_COLUMNS}
        structure = parse_drug_structure(columns) if columns["ingredient"] else None
        async with gate:
            start = time.monotonic()
            try:
                result = await run_pipeline(
                    initial_state(
                        source_id=-i,
                        source_value=case["case_id"],
                        source_name=case["source_name"],
                        domains=["Drug"],
                        drug_specific=True,
                        threshold=threshold,
                        drug_structure=structure,
                        domain_mode="prefer",
                    ),
                    ctx,
                    compiled=compiled,
                )
                row = score(case, result)
            except Exception as exc:  # report and go on
                row = {"correct": False, "accepted": False, "error": f"{type(exc).__name__}: {exc}"}
            row = {
                **{k: case[k] for k in ("case_id", "language", "kind", "source_name", "expected")},
                **row,
                "seconds": round(time.monotonic() - start, 1),
            }
        rows.append(row)
        mark = "ok " if row["correct"] else "BAD"
        print(
            f"{mark} {case['case_id']} {case['source_name'][:50]:50} -> {row.get('picked') or row.get('error', '')}",
            flush=True,
        )

    await asyncio.gather(*(one(i, c) for i, c in enumerate(cases, 1)))
    return sorted(rows, key=lambda r: r["case_id"])


def summary(rows: list[dict]) -> dict:
    def block(subset: list[dict]) -> dict:
        n = len(subset)
        accepted = [r for r in subset if r["accepted"]]
        return {
            "n": n,
            "correct": sum(r["correct"] for r in subset),
            "accuracy": round(sum(r["correct"] for r in subset) / n, 3) if n else None,
            "accepted": len(accepted),
            "accepted_wrong": sum(not r["correct"] for r in accepted),
            "to_review": n - len(accepted),
        }

    by: dict[str, dict] = {"all": block(rows)}
    for key in ("kind", "method", "language"):
        groups = defaultdict(list)
        for r in rows:
            groups[r.get(key) or "-"].append(r)
        by[key] = {k: block(v) for k, v in sorted(groups.items())}
    by["structured_step"] = {
        "accepted_by_match_drug": sum(r.get("method") == "auto_drug_structured" for r in rows),
        "approximate": sum(bool(r.get("approximate")) for r in rows),
        "errors": Counter(r["error"].split(":")[0] for r in rows if r.get("error")),
    }
    return by


def main() -> None:
    here = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cases", default=str(here / "eval" / "drug_synthetic_cases.csv"))
    parser.add_argument("--out", default="/tmp/drug_eval_results.csv")
    parser.add_argument("--threshold", type=int, default=8)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--only", help="comma-separated case ids")
    args = parser.parse_args()

    with open(args.cases, encoding="utf-8-sig", newline="") as fh:
        cases = list(csv.DictReader(fh))
    if args.only:
        wanted = set(args.only.split(","))
        cases = [c for c in cases if c["case_id"] in wanted]
    missing = check_expected(cases)
    if missing:
        print("Expected concepts not in the vocabulary (fix the cases):", *missing, sep="\n  ")
        sys.exit(1)

    rows = asyncio.run(evaluate(cases, args.threshold, args.concurrency))
    fields = list(dict.fromkeys(k for r in rows for k in r))
    with open(args.out, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fields)
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps(summary(rows), indent=2, default=dict))
    print(f"results: {args.out}")


if __name__ == "__main__":
    main()
