"""Sign-in through an OpenID Connect provider such as Keycloak (AUTH_MODE=oidc).

Authorization code flow with a confidential client and PKCE, run by the backend: the browser is
sent to the provider, comes back to /api/auth/oidc/callback with a code, and the backend
exchanges the code for tokens and verifies the ID token (signature from the provider's JWKS,
issuer, audience, expiry, nonce). The provider's tokens never reach the browser; the app then
issues its own session cookie as for a password sign-in.
"""

import base64
import hashlib
import logging
import secrets
import time
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import urlencode

import httpx
import jwt

from app.core.config import get_settings
from app.core.security import ALGORITHM

logger = logging.getLogger(__name__)

# Asymmetric algorithms only: an ID token signed with the client secret (HS*) is not accepted
ID_TOKEN_ALGORITHMS = ["RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384"]
LOGIN_STATE_SECONDS = 600
DISCOVERY_TTL_SECONDS = 3600
HTTP_TIMEOUT = 10.0

_discovery_cache: dict[str, tuple[float, dict]] = {}


class OidcError(Exception):
    """The provider's answer could not be used (logged; the user sees a generic error)."""


@dataclass(frozen=True)
class LoginState:
    """Kept in a short-lived, signed cookie between the redirect and the callback."""

    state: str
    nonce: str
    verifier: str


def _issuer() -> str:
    return get_settings().oidc_issuer_url.strip().rstrip("/")


def discovery() -> dict:
    """The provider's metadata (endpoints, JWKS), cached for an hour."""
    issuer = _issuer()
    cached = _discovery_cache.get(issuer)
    if cached and cached[0] > time.monotonic():
        return cached[1]
    try:
        resp = httpx.get(f"{issuer}/.well-known/openid-configuration", timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        doc = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise OidcError(f"Could not read the provider's discovery document: {exc}") from exc
    if str(doc.get("issuer", "")).rstrip("/") != issuer:
        raise OidcError("The discovery document names a different issuer than OIDC_ISSUER_URL")
    for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        if not doc.get(key):
            raise OidcError(f"The discovery document has no {key}")
    _discovery_cache[issuer] = (time.monotonic() + DISCOVERY_TTL_SECONDS, doc)
    return doc


@lru_cache(maxsize=4)
def _jwks_client(jwks_uri: str) -> jwt.PyJWKClient:
    # Caches the keys and fetches them again when a token names an unknown key (rotation)
    return jwt.PyJWKClient(jwks_uri, cache_keys=True, lifespan=DISCOVERY_TTL_SECONDS)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def new_login_state() -> LoginState:
    return LoginState(
        state=secrets.token_urlsafe(32),
        nonce=secrets.token_urlsafe(32),
        verifier=secrets.token_urlsafe(64),
    )


def encode_login_state(login: LoginState) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "state": login.state,
            "nonce": login.nonce,
            "verifier": login.verifier,
            "iat": now,
            "exp": now + LOGIN_STATE_SECONDS,
            "purpose": "oidc-login",
        },
        get_settings().jwt_secret,
        algorithm=ALGORITHM,
    )


def decode_login_state(value: str | None) -> LoginState | None:
    if not value:
        return None
    try:
        data = jwt.decode(value, get_settings().jwt_secret, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        return None
    if data.get("purpose") != "oidc-login":
        return None
    return LoginState(state=data["state"], nonce=data["nonce"], verifier=data["verifier"])


def authorization_url(login: LoginState, redirect_uri: str) -> str:
    settings = get_settings()
    challenge = _b64url(hashlib.sha256(login.verifier.encode()).digest())
    params = {
        "response_type": "code",
        "client_id": settings.oidc_client_id,
        "redirect_uri": redirect_uri,
        "scope": settings.oidc_scopes,
        "state": login.state,
        "nonce": login.nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return f"{discovery()['authorization_endpoint']}?{urlencode(params)}"


def exchange_code(code: str, redirect_uri: str, verifier: str) -> dict:
    """The token response for an authorization code (client_secret_basic authentication)."""
    settings = get_settings()
    try:
        resp = httpx.post(
            discovery()["token_endpoint"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": verifier,
            },
            auth=(settings.oidc_client_id, settings.oidc_client_secret),
            timeout=HTTP_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise OidcError(f"Token request failed: {exc}") from exc
    if resp.status_code != 200:
        # The provider's error code and description only (no secrets in them)
        try:
            body = resp.json()
            detail = f"{body.get('error')}: {body.get('error_description')}"
        except ValueError:
            detail = resp.text[:200]
        raise OidcError(f"Token request rejected ({resp.status_code}): {detail}")
    return resp.json()


def verify_id_token(id_token: str, nonce: str) -> dict:
    """The ID token's claims, after checking signature, issuer, audience, expiry and nonce."""
    settings = get_settings()
    try:
        key = _jwks_client(discovery()["jwks_uri"]).get_signing_key_from_jwt(id_token).key
        claims = jwt.decode(
            id_token,
            key,
            algorithms=ID_TOKEN_ALGORITHMS,
            audience=settings.oidc_client_id,
            issuer=_issuer(),
            leeway=60,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
    except (jwt.PyJWTError, jwt.PyJWKClientError) as exc:
        raise OidcError(f"Invalid ID token: {exc}") from exc
    if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
        raise OidcError("The ID token's nonce does not match this sign-in")
    # With several audiences the token must have been issued to this client
    if claims.get("azp") and claims["azp"] != settings.oidc_client_id:
        raise OidcError("The ID token was issued to another client (azp)")
    return claims


def sign_in_claims(code: str, redirect_uri: str, login: LoginState) -> dict:
    tokens = exchange_code(code, redirect_uri, login.verifier)
    id_token = tokens.get("id_token")
    if not id_token:
        raise OidcError("The token response has no ID token (is 'openid' in OIDC_SCOPES?)")
    return verify_id_token(id_token, login.nonce)
