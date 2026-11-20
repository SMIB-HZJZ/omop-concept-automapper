"""Test setup: a throwaway database on the compose Postgres (it needs pgvectorscale), Redis DB 15,
eager Celery, and deterministic fakes for every LLM call.

Run inside the dev stack:  docker compose -f docker-compose.dev.yaml exec backend pytest
"""

import hashlib
import io
import math
import os
import re
import uuid
import zipfile
from pathlib import Path

import psycopg
import pytest

FIXTURES = Path(__file__).parent / "fixtures"
_DB_NAME = f"test_automapper_{uuid.uuid4().hex[:8]}"
_PG = {
    "user": os.environ.get("POSTGRES_USER", "postgres"),
    "password": os.environ.get("POSTGRES_PASSWORD", "postgres"),
    "host": os.environ.get("POSTGRES_HOST", "localhost"),
    "port": os.environ.get("POSTGRES_PORT", "5432"),
}
TEST_DIMS = 64

os.environ.update(
    {
        "APP_ENV": "test",
        "DATABASE_URL": f"postgresql+psycopg://{_PG['user']}:{_PG['password']}@{_PG['host']}:{_PG['port']}/{_DB_NAME}",
        "REDIS_URL": os.environ.get("TEST_REDIS_URL", "redis://redis:6379/15"),
        "CELERY_TASK_ALWAYS_EAGER": "1",
        "UPLOAD_DIR": f"/tmp/{_DB_NAME}-uploads",
        "LLM_EMBEDDING_MODEL": "test/embedder",
        "LLM_CHAT_MODEL": "test/chat",
        "EMBEDDING_DIMS": str(TEST_DIMS),
        "JWT_SECRET": "test-secret-0123456789abcdef0123456789",
        "ADMIN_USERNAME": "admin",
        "ADMIN_PASSWORD": "admin-password",
        "AUTOMAP_CONCURRENCY": "3",
        "DISKANN_REBUILD_THRESHOLD": "5",
        "LOG_FORMAT": "text",
        "LLM_CACHE_TTL": "0",
        # Tests pin the simplest embedding text; the variants have their own tests
        "EMBEDDING_TEXT": "name",
        "EMBEDDING_TASK_TYPES": "false",
        "WORKFLOW_PLUGINS": "",
        "WORKFLOW_DEFINITIONS_DIR": "",
        "LLM_GATEWAY_URL": "http://127.0.0.1:9",  # nothing listens: any real LLM call fails fast
    }
)


def _admin_conn() -> psycopg.Connection:
    return psycopg.connect(dbname="postgres", autocommit=True, **{k: v for k, v in _PG.items()})


# --------------------------------------------------------------------------- fake LLMs


def fake_embedding(text: str, dims: int = TEST_DIMS) -> list[float]:
    """Deterministic bag-of-words vector: texts sharing words are close in cosine distance."""
    vector = [0.0] * dims
    for token in re.findall(r"[a-z0-9]+", text.lower()):
        digest = hashlib.md5(token.encode()).digest()
        vector[digest[0] % dims] += 1.0 if digest[1] % 2 else -1.0
        vector[digest[2] % dims] += 0.5
    norm = math.sqrt(sum(v * v for v in vector)) or 1.0
    return [v / norm for v in vector]


EMBED_CALLS: list[list[str]] = []


@pytest.fixture(scope="session", autouse=True)
def fake_embedder():
    from app.llm.embeddings import Embedder

    def embed(self, texts, kind="document"):
        EMBED_CALLS.append(list(texts))
        return [fake_embedding(t, self.dims or TEST_DIMS) for t in texts]

    async def aembed(self, texts, kind="document"):
        return embed(self, texts, kind)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(Embedder, "embed", embed)
        mp.setattr(Embedder, "aembed", aembed)
        yield EMBED_CALLS


class FakeReranker:
    """Picks the candidate with the most word overlap; confidence from a lookup or 9."""

    def __init__(self, confidence: int = 9, overrides: dict[str, int | None] | None = None):
        self.confidence = confidence
        self.overrides = overrides or {}
        self.calls: list[str] = []
        self.model = "fake/reranker"

    async def rerank(self, term, candidates, drug_specific, **kwargs):
        from app.llm.structured import RerankResult

        self.calls.append(term)
        if term in self.overrides and self.overrides[term] is None:
            return RerankResult(None, None)
        words = set(re.findall(r"[a-z0-9]+", term.lower()))
        best = max(
            range(len(candidates)),
            key=lambda i: len(
                words & set(re.findall(r"[a-z0-9]+", candidates[i]["concept_name"].lower()))
            ),
        )
        return RerankResult(best, self.overrides.get(term, self.confidence))


class _NoRewrites:
    async def rewrite(self, term, **kwargs):
        return []


class FakeTranslator:
    async def translate(self, term, context=None):
        from app.llm.structured import TranslationResult

        return TranslationResult(term, "en")


@pytest.fixture
def fake_reranker(monkeypatch):
    import app.workflows.service as service

    reranker = FakeReranker()
    monkeypatch.setattr(service, "Reranker", lambda model, effort=None: reranker)
    # The default pipeline translates; the fixture's terms are treated as English already
    monkeypatch.setattr(service, "Translator", lambda model, effort=None: FakeTranslator())
    # Unsure terms end with the refine agent; here it finds nothing (no real LLM call)
    from app.workflows.nodes import agentic
    from tests.workflows.helpers import no_match_agent

    monkeypatch.setattr(agentic, "run_mapping_agent", no_match_agent)
    from app.workflows.nodes import rewrite

    monkeypatch.setattr(rewrite, "QueryRewriter", lambda model, effort=None: _NoRewrites())
    from app.workflows.nodes import decide
    from tests.workflows.helpers import NoFinalPick

    monkeypatch.setattr(decide, "Reranker", lambda model, effort=None: NoFinalPick())
    # One fake answers every term; escalation to a second model would only call it twice
    monkeypatch.setattr(service.get_settings(), "rerank_escalate_model", "")
    return reranker


# --------------------------------------------------------------------------- database


@pytest.fixture(scope="session", autouse=True)
def database():
    with _admin_conn() as conn:
        conn.execute(f'CREATE DATABASE "{_DB_NAME}"')
    from app.cli import bootstrap, migrate
    from app.core.redis import get_redis

    migrate()
    bootstrap()
    get_redis().flushdb()
    yield
    from app.core.db import reset_engine

    reset_engine()
    with _admin_conn() as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{_DB_NAME}" WITH (FORCE)')


@pytest.fixture(scope="session")
def client(database):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


def _login(client, username: str, password: str) -> dict:
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    client.cookies.clear()  # tests authenticate with the bearer header only
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture(scope="session")
def admin_headers(client) -> dict:
    return _login(client, "admin", "admin-password")


@pytest.fixture(scope="session")
def user_headers(client, admin_headers) -> dict:
    resp = client.post(
        "/api/users",
        json={"username": "mapper", "password": "mapper-password", "role": "user"},
        headers=admin_headers,
    )
    assert resp.status_code == 201, resp.text
    return _login(client, "mapper", "mapper-password")


def vocabulary_zip(source: Path | None = None, prefix: str = "") -> bytes:
    """An Athena-style zip of the CSVs in ``source`` (default: the fixture vocabulary)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for csv in sorted((source or FIXTURES / "vocabulary").glob("*.csv")):
            zf.write(csv, f"{prefix}{csv.name}")
    return buf.getvalue()


def upload_vocabulary(client, headers, content: bytes | None = None, filename="athena.zip"):
    return client.post(
        "/api/vocabulary/import",
        content=vocabulary_zip() if content is None else content,
        params={"filename": filename},
        headers={**headers, "Content-Type": "application/zip"},
    )


@pytest.fixture(scope="session")
def loaded_vocabulary(client, admin_headers):
    """OMOP fixture vocabulary imported through the API (which also computes ATC7 codes)."""
    resp = upload_vocabulary(client, admin_headers)
    assert resp.status_code == 202, resp.text
    task = client.get(f"/api/tasks/{resp.json()['task_id']}", headers=admin_headers).json()
    assert task["status"] == "SUCCESS", task  # ATC7 codes are computed by the import
    return task


@pytest.fixture(scope="session")
def embedded(client, admin_headers, loaded_vocabulary):
    resp = client.post("/api/embeddings/embed", json={"batch_size": 10}, headers=admin_headers)
    assert resp.status_code == 202, resp.text
    task = client.get(f"/api/tasks/{resp.json()['task_id']}", headers=admin_headers).json()
    assert task["status"] == "SUCCESS", task
    return task


def import_source_csv(
    client, headers, vocabulary_id: int, content: bytes | None = None, **form
) -> dict:
    content = content if content is not None else (FIXTURES / "source_concepts.csv").read_bytes()
    existing = client.get("/api/source-vocabularies", headers=headers).json()
    if all(v["id"] != vocabulary_id for v in existing):
        form.setdefault("name", f"Test vocabulary {vocabulary_id}")  # a new vocabulary needs a name
    resp = client.post(
        f"/api/source-vocabularies/{vocabulary_id}/concepts/import",
        files={"file": ("source.csv", content, "text/csv")},
        data=form,
        headers=headers,
    )
    assert resp.status_code == 202, resp.text
    return client.get(f"/api/tasks/{resp.json()['task_id']}", headers=headers).json()


@pytest.fixture
def no_name_match(monkeypatch):
    """Turn off exact name/synonym matching and reuse of earlier mappings, for tests about the
    search + LLM path."""
    from app.services import lexical

    monkeypatch.setattr(lexical, "match_name", lambda names, vocabularies=None: lexical.TextMatch())
    monkeypatch.setattr(lexical, "match_previous", lambda name, exclude: lexical.TextMatch())
