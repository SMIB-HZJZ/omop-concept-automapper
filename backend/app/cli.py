"""Command line tools: ``python -m app.cli <command>``.

Commands:
  wait-db          wait until the database accepts connections
  migrate          adopt an existing Streamlit-era database (stamp 0001) and upgrade to head
  bootstrap        create the initial admin user if none exists
  init             wait-db + migrate + bootstrap (used by the container entrypoint)
  create-user      create a user: create-user <username> [--role admin] (password from prompt or --password)
  export-openapi   write the OpenAPI schema to a file (default: ../frontend/openapi.json)
  evaluate         measure mapping accuracy against existing mappings, e.g. with vs. without
                   translation: evaluate <vocabulary_id> --pipelines automap,automap_translated
"""

import argparse
import getpass
import json
import logging
import sys
import time
from pathlib import Path

from alembic.config import Config
from sqlalchemy import text

from alembic import command
from app.core.db import get_engine
from app.core.logging import setup_logging

logger = logging.getLogger("cli")
BACKEND_DIR = Path(__file__).resolve().parent.parent


def wait_db(max_attempts: int = 60, delay: float = 2.0) -> None:
    for attempt in range(1, max_attempts + 1):
        try:
            with get_engine().connect() as conn:
                conn.execute(text("SELECT 1"))
            logger.info("Database connection established")
            return
        except Exception as exc:
            logger.warning("Database not ready (%d/%d): %s", attempt, max_attempts, exc)
            time.sleep(delay)
    sys.exit("Database did not become ready")


def _alembic_config() -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    return cfg


def migrate() -> None:
    cfg = _alembic_config()
    with get_engine().connect() as conn:
        has_concept = (
            conn.execute(text("SELECT to_regclass('public.concept')")).scalar() is not None
        )
        has_version = (
            conn.execute(text("SELECT to_regclass('public.alembic_version')")).scalar() is not None
        )
    if has_concept and not has_version:
        logger.info("Existing database without migration history: stamping baseline 0001")
        command.stamp(cfg, "0001")
    command.upgrade(cfg, "head")
    with get_engine().connect() as conn:
        # Planner estimates: a COUNT(*) over millions of concepts would slow every start
        stats = dict(
            conn.execute(
                text(
                    "SELECT relname, GREATEST(reltuples, 0)::bigint FROM pg_class "
                    "WHERE relname IN ('concept', 'source_concepts', 'source_standard_map') "
                    "AND relkind = 'r'"
                )
            ).all()
        )
    logger.info("Database ready (approximate rows): %s", stats)


def bootstrap() -> None:
    from sqlmodel import Session

    from app.services.users import ensure_bootstrap_admin

    with Session(get_engine()) as session:
        ensure_bootstrap_admin(session)


def create_user(username: str, role: str, password: str | None) -> None:
    from sqlmodel import Session

    from app.services.users import create_user as do_create

    password = password or getpass.getpass(f"Password for {username}: ")
    with Session(get_engine()) as session:
        user = do_create(session, username, password, role)
    logger.info("Created %s user %s", user.role, user.username)


def export_openapi(path: str) -> None:
    from app.main import create_app

    schema = create_app().openapi()
    Path(path).write_text(json.dumps(schema, indent=2) + "\n")
    print(f"Wrote {path}")


def evaluate(args: argparse.Namespace) -> None:
    import asyncio
    import dataclasses
    from datetime import datetime

    from sqlmodel import Session

    from app.llm.structured import Reranker, Translator
    from app.models import SourceVocabulary
    from app.services import evaluation
    from app.services.settings_store import get_effective_config
    from app.workflows.service import build_context

    with Session(get_engine()) as session:
        vocab = session.get(SourceVocabulary, args.vocabulary_id)
        if vocab is None:
            sys.exit(f"Source vocabulary {args.vocabulary_id} not found")
        cfg = get_effective_config(session)
        items = evaluation.load_gold(
            session, args.vocabulary_id, args.sample, args.seed, args.include_unreviewed_auto
        )
    if not items:
        sys.exit("No mapped concepts to evaluate against in this vocabulary")
    drug = vocab.is_drug if args.drug is None else args.drug == "yes"
    domains = _split(args.domains) or sorted({d for i in items for d in i.gold_domains})
    pipelines = _split(args.pipelines)
    variants = evaluation.build_variants(pipelines, _split(args.llm_models), cfg)

    def make_context(llm_model: str):
        ctx = build_context(cfg)
        return dataclasses.replace(
            ctx,
            llm_model=llm_model,
            reranker=Reranker(llm_model),
            translator=Translator(llm_model),
        )

    logger.info(
        "Evaluating %d concepts of vocabulary %s (drug=%s, domains=%s, embeddings=%s)",
        len(items),
        vocab.name,
        drug,
        domains,
        cfg.collection_name,
    )
    summaries, per_variant, comparisons = asyncio.run(
        evaluation.evaluate(
            items,
            variants,
            make_context,
            domains=domains,
            drug_specific=drug,
            threshold=args.threshold,
            concurrency=args.concurrency,
        )
    )
    out = Path(args.out or f"/data/uploads/evaluation-{datetime.now():%Y%m%d-%H%M%S}")
    settings = {
        "vocabulary_id": args.vocabulary_id,
        "sample": len(items),
        "seed": args.seed,
        "drug_specific": drug,
        "domains": domains,
        "threshold": args.threshold,
        "embedding_collection": cfg.collection_name,
        "include_unreviewed_auto": args.include_unreviewed_auto,
    }
    summary_path, items_path = evaluation.write_report(
        out, summaries, per_variant, comparisons, items, settings
    )
    print(evaluation.format_table(summaries, comparisons))
    print(f"\nSummary: {summary_path}\nPer concept (contains source terms): {items_path}")


def _split(value: str | None) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("wait-db", "migrate", "bootstrap", "init"):
        sub.add_parser(name)
    cu = sub.add_parser("create-user")
    cu.add_argument("username")
    cu.add_argument("--role", choices=["user", "admin"], default="user")
    cu.add_argument("--password")
    eo = sub.add_parser("export-openapi")
    eo.add_argument(
        "path", nargs="?", default=str(BACKEND_DIR.parent / "frontend" / "openapi.json")
    )
    ev = sub.add_parser("evaluate", help="measure mapping accuracy against existing mappings")
    ev.add_argument("vocabulary_id", type=int)
    ev.add_argument("--pipelines", default="automap,automap_translated")
    ev.add_argument("--llm-models", help="comma-separated; default: the configured LLM model")
    ev.add_argument("--sample", type=int, default=200)
    ev.add_argument("--seed", type=int, default=42)
    ev.add_argument("--threshold", type=int, default=8)
    ev.add_argument("--domains", help="comma-separated; default: the domains of the gold concepts")
    ev.add_argument("--drug", choices=["yes", "no"], help="default: the vocabulary's drug flag")
    ev.add_argument("--concurrency", type=int, default=4)
    ev.add_argument(
        "--include-unreviewed-auto",
        action="store_true",
        help="also grade against mappings written by unreviewed auto-map runs",
    )
    ev.add_argument("--out", help="output directory (default: /data/uploads/evaluation-<time>)")
    args = parser.parse_args(argv)

    if args.command != "export-openapi":
        setup_logging()
    if args.command == "evaluate":
        evaluate(args)
    elif args.command == "wait-db":
        wait_db()
    elif args.command == "migrate":
        migrate()
    elif args.command == "bootstrap":
        bootstrap()
    elif args.command == "init":
        wait_db()
        migrate()
        bootstrap()
    elif args.command == "create-user":
        create_user(args.username, args.role, args.password)
    elif args.command == "export-openapi":
        export_openapi(args.path)


if __name__ == "__main__":
    main()
