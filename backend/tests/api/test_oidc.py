"""Sign-in through an OpenID Connect provider (AUTH_MODE=oidc) against a fake Keycloak."""

import base64
import hashlib
import time
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import get_engine
from app.services import oidc

ISSUER = "https://idp.example.test/realms/test"
CLIENT_ID = "automapper"
SECRET = "client-secret-for-tests"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class FakeProvider:
    """Discovery document and token endpoint; the ID token is built from ``claims``."""

    def __init__(self) -> None:
        self.claims: dict = {}
        self.key = KEY
        self.seen: dict = {}

    def id_token(self, nonce: str) -> str:
        now = int(time.time())
        payload = {
            "iss": ISSUER,
            "aud": CLIENT_ID,
            "azp": CLIENT_ID,
            "iat": now,
            "exp": now + 300,
            "nonce": nonce,
            **self.claims,
        }
        return jwt.encode(payload, self.key, algorithm="RS256", headers={"kid": "k1"})

    def get(self, url, timeout=None):
        assert url == f"{ISSUER}/.well-known/openid-configuration"
        doc = {
            "issuer": ISSUER,
            "authorization_endpoint": f"{ISSUER}/protocol/openid-connect/auth",
            "token_endpoint": f"{ISSUER}/protocol/openid-connect/token",
            "jwks_uri": f"{ISSUER}/protocol/openid-connect/certs",
        }
        return httpx.Response(200, json=doc, request=httpx.Request("GET", url))

    def post(self, url, data=None, auth=None, timeout=None):
        self.seen = {"data": data, "auth": auth}
        assert auth == (CLIENT_ID, SECRET)
        # PKCE: the verifier must match the challenge sent to the authorization endpoint
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(data["code_verifier"].encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        if data["code"] != "good-code" or challenge != self.seen_challenge:
            return httpx.Response(400, json={"error": "invalid_grant"})
        return httpx.Response(200, json={"id_token": self.id_token(self.seen_nonce)})


class FakeJwks:
    def get_signing_key_from_jwt(self, token):
        return type("Key", (), {"key": KEY.public_key()})()


@pytest.fixture()
def provider(monkeypatch):
    settings = get_settings()
    for name, value in {
        "auth_mode": "oidc",
        "oidc_issuer_url": ISSUER,
        "oidc_client_id": CLIENT_ID,
        "oidc_client_secret": SECRET,
        "oidc_initial_admin": "Boss@Example.test",
    }.items():
        monkeypatch.setattr(settings, name, value)
    fake = FakeProvider()
    monkeypatch.setattr(oidc.httpx, "get", fake.get)
    monkeypatch.setattr(oidc.httpx, "post", fake.post)
    monkeypatch.setattr(oidc, "_jwks_client", lambda uri: FakeJwks())
    oidc._discovery_cache.clear()
    yield fake
    oidc._discovery_cache.clear()


@pytest.fixture()
def browser(client):
    """A separate client with its own cookie jar (the shared one holds an admin session)."""
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


def sign_in(browser, provider, claims: dict, code: str = "good-code"):
    """Run the redirect to the provider and the callback; returns the callback response."""
    provider.claims = claims
    start = browser.get("/api/auth/oidc/login", follow_redirects=False)
    assert start.status_code == 303
    target = urlparse(start.headers["location"])
    assert f"{target.scheme}://{target.netloc}{target.path}" == (
        f"{ISSUER}/protocol/openid-connect/auth"
    )
    params = {k: v[0] for k, v in parse_qs(target.query).items()}
    assert params["client_id"] == CLIENT_ID and params["code_challenge_method"] == "S256"
    assert params["redirect_uri"] == "http://testserver/api/auth/oidc/callback"
    provider.seen_challenge = params["code_challenge"]
    provider.seen_nonce = params["nonce"]
    return browser.get(
        "/api/auth/oidc/callback",
        params={"code": code, "state": params["state"]},
        follow_redirects=False,
    )


def _user(username):
    with get_engine().connect() as conn:
        return (
            conn.execute(
                text("SELECT role, pending, oidc_subject FROM app_user WHERE lower(username) = :u"),
                {"u": username},
            )
            .mappings()
            .first()
        )


def test_options_and_password_routes_follow_the_mode(client, provider, admin_headers):
    assert client.get("/api/auth/options").json() == {
        "auth_mode": "oidc",
        "self_registration": False,
    }
    login = client.post("/api/auth/login", json={"username": "admin", "password": "x"})
    assert login.status_code == 403
    register = client.post(
        "/api/auth/register", json={"email": "new@example.test", "password": "12345678"}
    )
    assert register.status_code == 403
    create = client.post(
        "/api/users",
        json={"username": "someone", "password": "12345678"},
        headers=admin_headers,
    )
    assert create.status_code == 403


def test_accounts_are_approved_at_first_sign_in(provider, browser, admin_headers, client):
    # The initial admin (verified email, case-insensitive) becomes an admin
    resp = sign_in(
        browser, provider, {"sub": "s-boss", "email": "boss@example.test", "email_verified": True}
    )
    assert resp.status_code == 303 and resp.headers["location"] == "/"
    me = browser.get("/api/auth/me").json()
    assert me["username"] == "boss@example.test" and me["role"] == "admin"
    assert not browser.cookies.get("omop_oidc_login")  # the one-time state is gone

    # Anyone else gets an approved user account and a session at once
    other = type(browser)(browser.app)
    resp = sign_in(
        other, provider, {"sub": "s-ana", "email": "ana@example.test", "email_verified": True}
    )
    assert resp.headers["location"] == "/"
    assert other.get("/api/auth/me").json()["role"] == "user"
    assert dict(_user("ana@example.test")) == {
        "role": "user",
        "pending": False,
        "oidc_subject": "s-ana",
    }

    # The account is found by sub even if the email changed
    resp = sign_in(
        other, provider, {"sub": "s-ana", "email": "ana.new@example.test", "email_verified": True}
    )
    assert resp.headers["location"] == "/"
    assert other.get("/api/auth/me").json()["username"] == "ana@example.test"

    # A deactivated account gets no session
    users = client.get("/api/users", headers=admin_headers).json()
    ana = next(u for u in users if u["username"] == "ana@example.test")
    client.patch(f"/api/users/{ana['id']}", json={"is_active": False}, headers=admin_headers)
    third = type(browser)(browser.app)
    resp = sign_in(
        third, provider, {"sub": "s-ana", "email": "ana@example.test", "email_verified": True}
    )
    assert resp.headers["location"] == "/login?error=disabled"
    assert third.get("/api/auth/me").status_code == 401


def test_account_waiting_from_before_is_approved_on_sign_in(provider, browser):
    """Accounts created while OIDC sign-ins still waited for approval are approved now."""
    from app.services import users

    with get_engine().begin() as conn:
        conn.execute(
            text(
                "INSERT INTO app_user (username, hashed_password, role, pending, oidc_subject)"
                " VALUES ('old@example.test', :h, 'user', true, 's-old')"
            ),
            {"h": users.NO_PASSWORD},
        )
    resp = sign_in(
        browser, provider, {"sub": "s-old", "email": "old@example.test", "email_verified": True}
    )
    assert resp.headers["location"] == "/"
    assert _user("old@example.test")["pending"] is False


def test_unverified_email_cannot_take_a_colleagues_name(provider, browser, monkeypatch):
    """Someone who sets the first admin's email without verifying it gets their own name; the
    real admin is not locked out, and is promoted once their email is verified."""
    from app.services import users

    squatter = type(browser)(browser.app)
    resp = sign_in(
        squatter,
        provider,
        {
            "sub": "s-squat",
            "email": "chief@example.test",
            "email_verified": False,
            "preferred_username": "mallory",
        },
    )
    assert resp.headers["location"] == "/"
    me = squatter.get("/api/auth/me").json()
    assert me["username"] == "unverified:mallory" and me["role"] == "user"

    monkeypatch.setattr(get_settings(), "oidc_initial_admin", "chief@example.test")
    # The real person, email not yet verified: role user, not named by the email either
    resp = sign_in(
        browser,
        provider,
        {
            "sub": "s-chief",
            "email": "chief@example.test",
            "email_verified": False,
            "preferred_username": "chief",
        },
    )
    assert resp.headers["location"] == "/"
    me = browser.get("/api/auth/me").json()
    assert me["username"] == "unverified:chief" and me["role"] == "user"
    # Verified later, with no active admin around: named by the email and promoted
    monkeypatch.setattr(users, "count_admins", lambda session, oidc=False: 0)
    resp = sign_in(
        browser, provider, {"sub": "s-chief", "email": "chief@example.test", "email_verified": True}
    )
    assert resp.headers["location"] == "/"
    me = browser.get("/api/auth/me").json()
    assert me["username"] == "chief@example.test" and me["role"] == "admin"


def test_non_ascii_state_is_refused_cleanly(provider, browser):
    browser.get("/api/auth/oidc/login", follow_redirects=False)
    resp = browser.get(
        "/api/auth/oidc/callback", params={"code": "x", "state": "é"}, follow_redirects=False
    )
    assert resp.status_code == 303 and resp.headers["location"] == "/login?error=expired"


def test_forged_or_replayed_callbacks_are_refused(provider, browser):
    claims = {"sub": "s-x", "email": "x@example.test", "email_verified": True}
    # No sign-in was started in this browser (login CSRF): no state cookie
    resp = browser.get(
        "/api/auth/oidc/callback",
        params={"code": "good-code", "state": "x"},
        follow_redirects=False,
    )
    assert resp.headers["location"] == "/login?error=expired"

    # A code the provider rejects
    assert sign_in(browser, provider, claims, code="bad").headers["location"] == (
        "/login?error=provider"
    )

    # An ID token not signed by the provider's key
    # (the provider's JWKS holds only its real key)
    provider.key = OTHER_KEY
    assert sign_in(browser, provider, claims).headers["location"] == "/login?error=provider"
    provider.key = KEY

    # An ID token for another client
    assert (
        sign_in(browser, provider, {**claims, "aud": "other-app", "azp": "other-app"}).headers[
            "location"
        ]
        == "/login?error=provider"
    )

    # The person cancelled at the provider
    cancelled = browser.get(
        "/api/auth/oidc/callback", params={"error": "access_denied"}, follow_redirects=False
    )
    assert cancelled.headers["location"] == "/login?error=cancelled"
    assert browser.get("/api/auth/me").status_code == 401


def test_nonce_must_match(provider, browser):
    claims = {"sub": "s-n", "email": "n@example.test", "email_verified": True}
    provider.claims = claims
    start = browser.get("/api/auth/oidc/login", follow_redirects=False)
    params = {k: v[0] for k, v in parse_qs(urlparse(start.headers["location"]).query).items()}
    provider.seen_challenge = params["code_challenge"]
    provider.seen_nonce = "a-different-nonce"
    resp = browser.get(
        "/api/auth/oidc/callback",
        params={"code": "good-code", "state": params["state"]},
        follow_redirects=False,
    )
    assert resp.headers["location"] == "/login?error=provider"


def test_oidc_routes_are_off_in_local_mode(browser):
    assert get_settings().auth_mode == "local"
    assert browser.get("/api/auth/oidc/login", follow_redirects=False).status_code == 404
    assert browser.get("/api/auth/options").json()["auth_mode"] == "local"


def test_make_admin_command(client, admin_headers, capsys):
    from app.cli import main
    from app.services import users

    with get_engine().begin() as conn:
        conn.execute(
            text(
                "INSERT INTO app_user (username, hashed_password, role, pending)"
                " VALUES ('rescue@example.test', :h, 'user', true)"
            ),
            {"h": users.NO_PASSWORD},
        )
    main(["make-admin", "Rescue@example.test"])
    assert dict(_user("rescue@example.test")) == {
        "role": "admin",
        "pending": False,
        "oidc_subject": None,
    }
    with pytest.raises(SystemExit):
        main(["make-admin", "nobody@example.test"])


def test_email_verified_as_string_and_password_admins_do_not_block_promotion(
    provider, browser, monkeypatch, admin_headers
):
    """The local test admin (no provider account) cannot sign in under OIDC, so it does not
    stop the initial admin from being promoted once their email is verified."""
    monkeypatch.setattr(get_settings(), "oidc_initial_admin", "late@example.test")
    with get_engine().begin() as conn:
        # Only password admins exist besides this person
        others = (
            conn.execute(
                text(
                    "UPDATE app_user SET role = 'user' WHERE role = 'admin' AND oidc_subject IS NOT NULL"
                    " RETURNING id"
                )
            )
            .scalars()
            .all()
        )
    try:
        resp = sign_in(
            browser,
            provider,
            {"sub": "s-late", "email": "late@example.test", "email_verified": False},
        )
        assert resp.headers["location"] == "/"
        assert browser.get("/api/auth/me").json()["role"] == "user"
        # Some providers send the string "true"
        resp = sign_in(
            browser,
            provider,
            {"sub": "s-late", "email": "late@example.test", "email_verified": "true"},
        )
        me = browser.get("/api/auth/me").json()
        assert me["username"] == "late@example.test" and me["role"] == "admin"
    finally:
        with get_engine().begin() as conn:
            conn.execute(
                text("UPDATE app_user SET role = 'admin' WHERE id = ANY(:ids)"), {"ids": others}
            )
