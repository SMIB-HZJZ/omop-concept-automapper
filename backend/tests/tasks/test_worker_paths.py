"""Worker paths that the API-level tests do not reach: cleanup after cancel/failure, connection
reuse across collections and event loops, and the non-blocking vocabulary swap."""

import asyncio

import openai
import pytest
from sqlalchemy import text

from app.core.db import get_engine, raw_connection
from app.llm.embeddings import Embedder

REAL_EMBED = Embedder.embed  # captured at import, before conftest's session-wide fake


def _status(client, headers, resp):
    return client.get(f"/api/tasks/{resp.json()['task_id']}", headers=headers).json()


def test_cancelled_embedding_still_builds_the_index(client, admin_headers, embedded, monkeypatch):
    """A cancel during a bulk load (index dropped first) must leave the DiskANN index in place."""
    from app.vector import store
    from app.worker.progress import ProgressReporter

    client.put("/api/config", json={"embedding_dims": 48}, headers=admin_headers)
    try:
        calls = {"n": 0}

        def cancel_after_first_batch(self):
            # 1: "Checking the embedding model", 2: "Embedding …" phase, 3: after batch 1
            calls["n"] += 1
            return calls["n"] >= 3

        monkeypatch.setattr(ProgressReporter, "is_cancelled", cancel_after_first_batch)
        resp = client.post("/api/embeddings/embed", json={"batch_size": 10}, headers=admin_headers)
        task = _status(client, admin_headers, resp)
        assert task["status"] == "CANCELLED", task
        monkeypatch.undo()

        from sqlmodel import Session

        with Session(get_engine()) as session:
            assert store.has_index(session, "emb_test_embedder_48")
        status = client.get("/api/embeddings/status", headers=admin_headers).json()
        assert 0 < status["embedded"] < status["total"]
    finally:
        client.delete(
            "/api/embeddings", params={"confirm": "test_embedder_48"}, headers=admin_headers
        )
        client.post("/api/config/reset", headers=admin_headers)


def test_bulk_upsert_handles_different_vector_sizes_on_one_connection(loaded_vocabulary):
    """The staging table must not keep the vector size of the first collection loaded."""
    from sqlmodel import Session

    from app.vector import store

    with Session(get_engine()) as session:
        small = store.ensure_collection(session, "stage_test_8", "test/embedder", 8).table_name
        large = store.ensure_collection(session, "stage_test_16", "test/embedder", 16).table_name
    try:
        with raw_connection() as conn:
            assert store.bulk_upsert(conn, small, [(1, [0.1] * 8, [1])]) == 1
            assert store.bulk_upsert(conn, large, [(1, [0.1] * 16, [1])]) == 1
    finally:
        with Session(get_engine()) as session:
            store.drop_collection(session, "stage_test_8")
            store.drop_collection(session, "stage_test_16")


def test_async_client_is_per_event_loop():
    """Each Celery task runs its own asyncio.run loop; it must not reuse a closed loop's client."""
    from app.llm import gateway

    async def grab():
        client = gateway.get_async_client()
        assert gateway.get_async_client() is client  # reused within the loop
        await gateway.aclose_async_client()
        return client

    first = asyncio.run(grab())
    second = asyncio.run(grab())
    assert first is not second
    assert first.is_closed() and second.is_closed()


def test_vocabulary_reimport_swaps_tables_and_keeps_indexes(
    client, admin_headers, loaded_vocabulary
):
    def index_names(table):
        with get_engine().connect() as conn:
            return set(
                conn.execute(
                    text("SELECT indexname FROM pg_indexes WHERE tablename = :t"), {"t": table}
                ).scalars()
            )

    before = index_names("concept_ancestor")
    resp = client.post("/api/vocabulary/import", headers=admin_headers)
    assert _status(client, admin_headers, resp)["status"] == "SUCCESS"
    assert index_names("concept_ancestor") == before
    with get_engine().connect() as conn:
        leftovers = conn.execute(
            text(
                "SELECT to_regclass('concept_ancestor_new'), to_regclass('concept_relationship_new')"
            )
        ).one()
        fks = conn.execute(
            text(
                "SELECT conname FROM pg_constraint "
                "WHERE conrelid = 'concept_ancestor'::regclass AND contype = 'f'"
            )
        ).scalars()
        assert set(fks) == {
            "fpk_concept_ancestor_ancestor_concept_id",
            "fpk_concept_ancestor_descendant_concept_id",
        }
    assert leftovers == (None, None)


def test_vocabulary_import_with_a_failing_table_fails(
    client, admin_headers, loaded_vocabulary, monkeypatch
):
    from app.services import vocabulary_import

    def broken(*args, **kwargs):
        raise RuntimeError("bad file")

    monkeypatch.setattr(vocabulary_import, "import_truncate_copy", broken)
    resp = client.post("/api/vocabulary/import", headers=admin_headers)
    task = _status(client, admin_headers, resp)
    assert task["status"] == "FAILURE"
    assert "concept_relationship" in task["error"] and "concept_ancestor" in task["error"]
    # The result still says what did import
    assert task["result"]["tables"]["concept"]["status"] == "success"


@pytest.mark.parametrize(
    "value,expected",
    [
        ("A10BA02-001", ["A10BA02"]),
        (" n02be01 tbl", ["N02BE01"]),
        ("A10BA0", []),  # too short
        ("XA10BA02", []),  # must be at the start
        ("", []),
        (None, []),
    ],
)
def test_atc7_extraction(value, expected):
    from app.services.atc7 import extract_atc7_codes_from_source

    assert extract_atc7_codes_from_source(value) == expected


def _status_error(cls, status):
    import httpx

    request = httpx.Request("POST", "http://gateway/v1/embeddings")
    return cls("boom", response=httpx.Response(status, request=request), body=None)


class _FailingClient:
    """Stands in for the OpenAI client: every embeddings.create raises ``error``."""

    def __init__(self, error: Exception):
        self.error = error
        self.calls = 0
        self.embeddings = self

    def create(self, **kwargs):
        self.calls += 1
        raise self.error


def test_embedder_retries_only_transient_errors(monkeypatch):
    from app.core.config import get_settings
    from app.llm import embeddings

    monkeypatch.setattr(get_settings(), "embedding_max_retries", 3)
    monkeypatch.setattr("tenacity.nap.time.sleep", lambda _: None)
    # conftest replaces Embedder.embed with a fake; test the real implementation
    monkeypatch.setattr(embeddings.Embedder, "embed", REAL_EMBED)
    embedder = embeddings.Embedder("test/embedder", 8)

    for error, expected_calls in (
        (_status_error(openai.RateLimitError, 429), 3),
        (_status_error(openai.AuthenticationError, 401), 1),
        (_status_error(openai.BadRequestError, 400), 1),
    ):
        fake = _FailingClient(error)
        monkeypatch.setattr(embeddings, "get_sync_client", lambda fake=fake: fake)
        with pytest.raises(type(error)):
            embedder.embed(["x"])
        assert fake.calls == expected_calls, type(error).__name__


def test_progress_accepts_byte_counts_above_int32(admin_headers, client):
    """A full Athena release is ~3.5 GB; the import reports progress in bytes."""
    from sqlalchemy import text

    from app.core.db import session_scope

    with session_scope() as session:
        session.execute(
            text(
                "INSERT INTO task_run (id, kind, status, progress_current, progress_total) "
                "VALUES ('big-progress', 'vocabulary.import', 'PROGRESS', 3245667766, 3501385512)"
            )
        )
        session.commit()
        assert (
            session.execute(
                text("SELECT progress_total FROM task_run WHERE id = 'big-progress'")
            ).scalar_one()
            == 3501385512
        )
        session.execute(text("DELETE FROM task_run WHERE id = 'big-progress'"))
        session.commit()
