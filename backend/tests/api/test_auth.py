from datetime import UTC, datetime, timedelta

import jwt
import pytest

ADMIN_ONLY = [
    ("post", "/api/vocabulary/import", None),
    ("post", "/api/embeddings/embed", {"batch_size": 100}),
    ("post", "/api/embeddings/sync-metadata", None),
    ("delete", "/api/embeddings", None),
    ("put", "/api/config", {"llm_model": "x/y"}),
    ("post", "/api/config/reset", None),
    ("delete", "/api/source-vocabularies/1", None),
    ("get", "/api/users", None),
    ("post", "/api/users", {"username": "zzz", "password": "12345678"}),
    ("patch", "/api/users/1", {"is_active": False}),
    ("delete", "/api/users/1", None),
]


def test_health_is_public(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["checks"]["database"] is True


def test_endpoints_require_authentication(client):
    for path in ("/api/meta", "/api/source-vocabularies", "/api/tasks", "/api/config"):
        assert client.get(path).status_code == 401


def test_bootstrap_admin_and_me(client, admin_headers):
    me = client.get("/api/auth/me", headers=admin_headers).json()
    assert me["username"] == "admin" and me["role"] == "admin"


def test_login_sets_httponly_cookie(client):
    resp = client.post("/api/auth/login", json={"username": "admin", "password": "admin-password"})
    assert resp.status_code == 200
    cookie = resp.headers["set-cookie"]
    assert "omop_session=" in cookie and "HttpOnly" in cookie and "samesite=lax" in cookie.lower()
    # The cookie alone authenticates
    me = client.get("/api/auth/me")
    assert me.status_code == 200
    client.post("/api/auth/logout")
    client.cookies.clear()
    assert client.get("/api/auth/me").status_code == 401


def test_wrong_password_and_lockout(client):
    for _ in range(5):
        resp = client.post("/api/auth/login", json={"username": "lockme", "password": "nope"})
        assert resp.status_code == 401
    resp = client.post("/api/auth/login", json={"username": "lockme", "password": "nope"})
    assert resp.status_code == 429


@pytest.mark.parametrize("method,path,body", ADMIN_ONLY)
def test_admin_only_endpoints_forbid_users(client, user_headers, method, path, body):
    kwargs = {"headers": user_headers}
    if body is not None:
        kwargs["json"] = body
    resp = getattr(client, method)(path, **kwargs)
    assert resp.status_code == 403, (path, resp.text)


def test_expired_token_rejected(client, user_headers):
    from app.core.config import get_settings

    token = jwt.encode(
        {"sub": "1", "role": "admin", "exp": datetime.now(UTC) - timedelta(minutes=1)},
        get_settings().jwt_secret,
        algorithm="HS256",
    )
    assert (
        client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    )


def test_user_management_and_deactivation(client, admin_headers):
    resp = client.post(
        "/api/users",
        json={"username": "temp_user", "password": "temp-password", "role": "user"},
        headers=admin_headers,
    )
    assert resp.status_code == 201
    user_id = resp.json()["id"]
    assert (
        client.post(
            "/api/users",
            json={"username": "TEMP_user", "password": "temp-password"},
            headers=admin_headers,
        ).status_code
        == 409
    )

    login = client.post(
        "/api/auth/login", json={"username": "temp_user", "password": "temp-password"}
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    client.cookies.clear()
    assert client.get("/api/auth/me", headers=headers).status_code == 200

    # Deactivation applies immediately to existing tokens
    resp = client.patch(f"/api/users/{user_id}", json={"is_active": False}, headers=admin_headers)
    assert resp.json()["is_active"] is False
    assert client.get("/api/auth/me", headers=headers).status_code == 401

    assert client.delete(f"/api/users/{user_id}", headers=admin_headers).status_code == 200


def test_last_admin_is_protected(client, admin_headers):
    me = client.get("/api/auth/me", headers=admin_headers).json()
    resp = client.patch(f"/api/users/{me['id']}", json={"role": "user"}, headers=admin_headers)
    assert resp.status_code == 400
    assert client.delete(f"/api/users/{me['id']}", headers=admin_headers).status_code == 400


def test_change_password(client):
    client.cookies.clear()
    admin = client.post(
        "/api/auth/login", json={"username": "admin", "password": "admin-password"}
    ).json()
    headers = {"Authorization": f"Bearer {admin['access_token']}"}
    client.post(
        "/api/users", json={"username": "pw_user", "password": "first-password"}, headers=headers
    )
    token = client.post(
        "/api/auth/login", json={"username": "pw_user", "password": "first-password"}
    ).json()
    h = {"Authorization": f"Bearer {token['access_token']}"}
    client.cookies.clear()
    bad = client.post(
        "/api/auth/change-password",
        json={"current_password": "x", "new_password": "second-password"},
        headers=h,
    )
    assert bad.status_code == 400
    ok = client.post(
        "/api/auth/change-password",
        json={"current_password": "first-password", "new_password": "second-password"},
        headers=h,
    )
    assert ok.status_code == 200
    assert (
        client.post(
            "/api/auth/login", json={"username": "pw_user", "password": "second-password"}
        ).status_code
        == 200
    )
    client.cookies.clear()


def test_password_change_revokes_other_sessions(client, admin_headers):
    client.post(
        "/api/users",
        json={"username": "rev_user", "password": "first-password"},
        headers=admin_headers,
    )
    client.cookies.clear()
    other = client.post(
        "/api/auth/login", json={"username": "rev_user", "password": "first-password"}
    ).json()["access_token"]
    client.cookies.clear()
    this = client.post(
        "/api/auth/login", json={"username": "rev_user", "password": "first-password"}
    ).json()["access_token"]
    client.cookies.clear()
    resp = client.post(
        "/api/auth/change-password",
        json={"current_password": "first-password", "new_password": "second-password"},
        headers={"Authorization": f"Bearer {this}"},
    )
    assert resp.status_code == 200
    # The response carries a fresh session cookie for the current browser
    assert "omop_session=" in resp.headers["set-cookie"]
    assert client.get("/api/auth/me").status_code == 200
    client.cookies.clear()
    # Both old tokens (including the other device's) are dead
    for token in (other, this):
        assert (
            client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code
            == 401
        )


def test_lockout_cannot_be_bypassed_by_rotating_ip(client, admin_headers, monkeypatch):
    from app.core.config import get_settings
    from app.services import users

    monkeypatch.setattr(get_settings(), "login_max_failures_per_user", 6)
    from sqlmodel import Session

    from app.core.db import get_engine

    with Session(get_engine()) as session:
        for i in range(6):
            # A different client IP for each attempt, as with a spoofed X-Forwarded-For
            assert users.authenticate(session, "admin", "wrong", f"10.0.0.{i}") is None
        with pytest.raises(users.LoginLocked):
            users.authenticate(session, "admin", "admin-password", "10.0.0.99")
    from app.core.redis import get_redis

    get_redis().delete("login:fail:admin", *[f"login:fail:admin:10.0.0.{i}" for i in range(6)])


def test_unknown_user_still_hashes(client, monkeypatch):
    from app.core import security

    calls = {"n": 0}
    original = security.verify_password

    def counting(password, hashed):
        calls["n"] += 1
        return original(password, hashed)

    monkeypatch.setattr(security, "verify_password", counting)
    client.post("/api/auth/login", json={"username": "nobody-here", "password": "x"})
    assert calls["n"] == 1


def test_prod_refuses_weak_secrets(monkeypatch):
    import pydantic

    from app.core.config import Settings

    strong = {
        "APP_ENV": "prod",
        "JWT_SECRET": "x" * 40,
        "ADMIN_PASSWORD": "a-strong-admin-password",
        "LITELLM_MASTER_KEY": "sk-real-key",
        "DATABASE_URL": "postgresql+psycopg://app:s3cret@db:5432/x",
    }
    for key, value in strong.items():
        monkeypatch.setenv(key, value)
    # The dev stack sets COOKIE_SECURE=false in the container; prod derives it from APP_ENV
    monkeypatch.delenv("COOKIE_SECURE", raising=False)
    settings = Settings(_env_file=None)
    assert settings.secure_cookies is True
    for key, weak, reported in (
        ("JWT_SECRET", "too-short", "JWT_SECRET"),
        ("LITELLM_MASTER_KEY", "sk-litellm-change-me", "LITELLM_MASTER_KEY"),
        ("DATABASE_URL", "postgresql+psycopg://postgres:postgres@db:5432/x", "POSTGRES_PASSWORD"),
    ):
        monkeypatch.setenv(key, weak)
        with pytest.raises(pydantic.ValidationError, match=reported):
            Settings(_env_file=None)
        monkeypatch.setenv(key, strong[key])
