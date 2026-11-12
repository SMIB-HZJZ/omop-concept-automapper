from app.services import tasks as task_service


def test_task_visibility(client, user_headers, admin_headers, loaded_vocabulary):
    # Admin tasks are not visible to a regular user
    admin_tasks = client.get("/api/tasks", headers=admin_headers).json()
    assert admin_tasks
    user_view = client.get("/api/tasks", headers=user_headers).json()
    assert all(t["username"] == "mapper" for t in user_view)
    assert (
        client.get(f"/api/tasks/{admin_tasks[-1]['id']}", headers=user_headers).status_code == 404
    )


def test_cancel_pending_task(client, admin_headers):
    from sqlmodel import Session

    from app.core.db import get_engine
    from app.models import TaskRun

    with Session(get_engine()) as session:
        session.add(TaskRun(id="pending-task", kind="test.kind", status="PENDING"))
        session.commit()
    resp = client.post("/api/tasks/pending-task/cancel", headers=admin_headers)
    assert resp.json()["status"] == "CANCELLED" and resp.json()["is_terminal"]


def test_lock_conflict(client, admin_headers):
    from sqlmodel import Session

    from app.core.db import get_engine
    from app.core.redis import get_redis
    from app.models import TaskRun
    from app.worker.progress import heartbeat_key

    with Session(get_engine()) as session:
        session.add(TaskRun(id="running-embed", kind="embeddings.embed", status="PROGRESS"))
        session.commit()
    get_redis().set(heartbeat_key("running-embed"), "1", ex=60)
    try:
        resp = client.post("/api/embeddings/sync-metadata", headers=admin_headers)
        assert resp.status_code == 409
        active = client.get("/api/tasks", params={"active": True}, headers=admin_headers).json()
        assert any(t["id"] == "running-embed" for t in active)
        # A live task is never treated as orphaned
        assert task_service.fail_orphaned_tasks() == 0
    finally:
        # Its worker "dies": the heartbeat expires and the task no longer blocks the group
        get_redis().delete(heartbeat_key("running-embed"))
    resp = client.post("/api/embeddings/sync-metadata", headers=admin_headers)
    assert resp.status_code == 202
    task = client.get("/api/tasks/running-embed", headers=admin_headers).json()
    assert task["status"] == "FAILURE" and "worker stopped" in task["error"]


def test_task_lock_released_and_heartbeat_cleared(client, admin_headers):
    from app.core.redis import get_redis
    from app.worker.base import lock_key
    from app.worker.progress import heartbeat_key

    resp = client.post("/api/embeddings/sync-atc7", headers=admin_headers)
    task_id = resp.json()["task_id"]
    assert client.get(f"/api/tasks/{task_id}", headers=admin_headers).json()["status"] == "SUCCESS"
    assert not get_redis().exists(lock_key("embed"))
    assert not get_redis().exists(heartbeat_key(task_id))


def test_broker_failure_marks_task_failed(client, admin_headers, monkeypatch):
    from app.worker.tasks.embeddings import sync_atc7

    def broken(*args, **kwargs):
        raise ConnectionError("broker down")

    monkeypatch.setattr(sync_atc7, "apply_async", broken)
    resp = client.post("/api/embeddings/sync-atc7", headers=admin_headers)
    assert resp.status_code == 503
    failed = client.get(
        "/api/tasks",
        params={"kind": "embeddings.sync_atc7", "active": False},
        headers=admin_headers,
    ).json()[0]
    assert failed["status"] == "FAILURE" and "broker down" in failed["error"]
    # The failed launch does not block the next one
    monkeypatch.undo()
    assert client.post("/api/embeddings/sync-atc7", headers=admin_headers).status_code == 202


def test_cooperative_cancel(client, user_headers, embedded, fake_reranker, monkeypatch):
    """Cancelling a running auto-map stops it between concepts and keeps what was saved."""
    from app.worker.progress import ProgressReporter
    from tests.conftest import import_source_csv

    import_source_csv(client, user_headers, 30)
    calls = {"n": 0}

    def cancelled_after_two(self):
        calls["n"] += 1
        return calls["n"] >= 2

    monkeypatch.setattr(ProgressReporter, "is_cancelled", cancelled_after_two)
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "automap_concurrency", 1)
    resp = client.post(
        "/api/source-vocabularies/30/automap",
        json={"domains": ["Drug", "Condition"], "mode": "commit"},
        headers=user_headers,
    )
    task = client.get(f"/api/tasks/{resp.json()['task_id']}", headers=user_headers).json()
    assert task["status"] == "CANCELLED"
    assert "Cancelled after" in task["message"]
    stats = client.get("/api/source-vocabularies/30/stats", headers=user_headers).json()
    assert 0 < stats["mapped"] - 2 < 4
