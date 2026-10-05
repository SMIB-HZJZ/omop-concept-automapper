from datetime import UTC, datetime, timedelta

import jwt
import pytest

ADMIN_ONLY = [
    ("post", "/api/vocabulary/import", None),
    ("post", "/api/atc7/process", None),
    ("post", "/api/embeddings/embed", {"batch_size": 100}),
    ("post", "/api/embeddings/sync-metadata", None),
    ("post", "/api/embeddings/sync-atc7", None),
    ("delete", "/api/embeddings", None),
    ("post", "/api/embeddings/migrate-from-qdrant", {}),
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
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_user_management_and_deactivation(client, admin_headers):
    resp = client.post(
        "/api/users",
        json={"username": "temp_user", "password": "temp-password", "role": "user"},
        headers=admin_headers,
    )
    assert resp.status_code == 201
    user_id = resp.json()["id"]
    assert client.post(
        "/api/users", json={"username": "TEMP_user", "password": "temp-password"}, headers=admin_headers
    ).status_code == 409

    login = client.post("/api/auth/login", json={"username": "temp_user", "password": "temp-password"})
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
    admin = client.post("/api/auth/login", json={"username": "admin", "password": "admin-password"}).json()
    headers = {"Authorization": f"Bearer {admin['access_token']}"}
    client.post("/api/users", json={"username": "pw_user", "password": "first-password"}, headers=headers)
    token = client.post("/api/auth/login", json={"username": "pw_user", "password": "first-password"}).json()
    h = {"Authorization": f"Bearer {token['access_token']}"}
    client.cookies.clear()
    bad = client.post("/api/auth/change-password", json={"current_password": "x", "new_password": "second-password"}, headers=h)
    assert bad.status_code == 400
    ok = client.post(
        "/api/auth/change-password",
        json={"current_password": "first-password", "new_password": "second-password"},
        headers=h,
    )
    assert ok.status_code == 200
    assert client.post("/api/auth/login", json={"username": "pw_user", "password": "second-password"}).status_code == 200
    client.cookies.clear()
