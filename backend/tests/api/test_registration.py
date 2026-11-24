"""Self-registration: an account from the sign-in page signs in only after an admin approves it."""

import pytest

from app.core.redis import get_redis


@pytest.fixture(autouse=True)
def _reset_limits():
    redis = get_redis()
    for key in redis.scan_iter("register:ip:*"):
        redis.delete(key)
    yield
    for key in redis.scan_iter("register:ip:*"):
        redis.delete(key)


def _register(client, email, password="new-user-pw"):
    return client.post("/api/auth/register", json={"email": email, "password": password})


def _login(client, username, password="new-user-pw"):
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    client.cookies.clear()
    return resp


def _user(client, admin_headers, username):
    return next(
        u
        for u in client.get("/api/users", headers=admin_headers).json()
        if u["username"] == username
    )


def test_registered_account_signs_in_only_after_approval(client, admin_headers):
    assert client.get("/api/auth/options").json() == {"self_registration": True}

    resp = _register(client, "Ana.Example@Example.org")
    assert resp.status_code == 201, resp.text
    assert "approve" in resp.json()["message"]

    # Stored in lower case, as a user waiting for approval
    user = _user(client, admin_headers, "ana.example@example.org")
    assert user["pending"] and user["role"] == "user" and user["approved_at"] is None

    # The right password is told the account waits; a wrong one is an ordinary failure
    pending = _login(client, "ana.example@example.org")
    assert pending.status_code == 403 and "approval" in pending.json()["detail"]
    assert _login(client, "ana.example@example.org", "wrong-pw-1").status_code == 401

    approved = client.post(f"/api/users/{user['id']}/approve", headers=admin_headers)
    assert approved.status_code == 200 and approved.json()["pending"] is False
    assert approved.json()["approved_at"] is not None
    assert client.post(f"/api/users/{user['id']}/approve", headers=admin_headers).status_code == 409

    ok = _login(client, "ana.example@example.org")
    assert ok.status_code == 200 and ok.json()["user"]["pending"] is False
    get_redis().delete(
        "login:fail:ana.example@example.org", "login:fail:ana.example@example.org:testclient"
    )


def test_registration_checks_input_and_duplicates(client, admin_headers):
    assert _register(client, "not-an-email").status_code == 422
    assert _register(client, "short@x.io", "1234567").status_code == 422
    assert _register(client, "dup@example.org").status_code == 201
    dup = _register(client, "DUP@example.org")
    assert dup.status_code == 409
    # An admin rejects a registration by deleting it
    user = _user(client, admin_headers, "dup@example.org")
    assert client.delete(f"/api/users/{user['id']}", headers=admin_headers).status_code == 200


def test_registration_is_limited_per_ip_and_can_be_turned_off(client, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "registrations_per_ip_hour", 2)
    assert _register(client, "one@example.org").status_code == 201
    assert _register(client, "two@example.org").status_code == 201
    assert _register(client, "three@example.org").status_code == 429

    monkeypatch.setattr(get_settings(), "self_registration", False)
    assert client.get("/api/auth/options").json() == {"self_registration": False}
    assert _register(client, "four@example.org").status_code == 403


def test_approve_is_admin_only(client, user_headers):
    assert client.post("/api/users/1/approve", headers=user_headers).status_code == 403
