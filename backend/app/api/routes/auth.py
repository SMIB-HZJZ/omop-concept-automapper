import logging
import secrets

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse

from app.api.deps import CurrentUser, SessionDep, require_local_auth
from app.core.config import get_settings
from app.core.security import create_access_token, hash_password, verify_password
from app.models import AppUser
from app.schemas.api import (
    AuthOptions,
    ChangePassword,
    LoginRequest,
    LoginResponse,
    Message,
    RegisterRequest,
    UserOut,
)
from app.services import oidc, users

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

OIDC_STATE_COOKIE = "omop_oidc_login"
OIDC_COOKIE_PATH = "/api/auth/oidc"


def _issue_session(response: Response, user: AppUser) -> tuple[str, int]:
    token, expires_in = create_access_token(user.id, user.role, user.token_version)  # type: ignore[arg-type]
    settings = get_settings()
    response.set_cookie(
        settings.cookie_name,
        token,
        max_age=expires_in,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
        path="/",
    )
    return token, expires_in


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest, request: Request, response: Response, session: SessionDep):
    require_local_auth()
    ip = request.client.host if request.client else "unknown"
    try:
        user = users.authenticate(session, body.username, body.password, ip)
    except users.LoginLocked:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "Too many failed attempts, try again later"
        ) from None
    except users.AccountPending:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Your account is waiting for an administrator's approval",
        ) from None
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid username or password")
    token, expires_in = _issue_session(response, user)
    return LoginResponse(
        access_token=token, expires_in=expires_in, user=UserOut.model_validate(user)
    )


@router.get("/options", response_model=AuthOptions)
def options():
    """What the sign-in page offers (public)."""
    settings = get_settings()
    local = settings.auth_mode == "local"
    return AuthOptions(
        auth_mode=settings.auth_mode, self_registration=local and settings.self_registration
    )


@router.post("/register", response_model=Message, status_code=status.HTTP_201_CREATED)
def register(body: RegisterRequest, request: Request, session: SessionDep):
    """Create an account that can sign in once an admin approves it on the Users page."""
    require_local_auth()
    if not get_settings().self_registration:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Accounts are created by an administrator")
    ip = request.client.host if request.client else "unknown"
    try:
        users.register(session, body.email, body.password, ip)
    except users.RegistrationLimited:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "Too many new accounts from here, try again later"
        ) from None
    except ValueError:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "An account with this email already exists"
        ) from None
    return Message(
        message="Account created. An administrator must approve it before you can sign in."
    )


@router.post("/logout", response_model=Message)
def logout(response: Response):
    response.delete_cookie(get_settings().cookie_name, path="/")
    return Message(message="Logged out")


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser):
    return user


@router.post("/change-password", response_model=Message)
def change_password(
    body: ChangePassword, user: CurrentUser, session: SessionDep, response: Response
):
    require_local_auth()
    if not verify_password(body.current_password, user.hashed_password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect")
    user.hashed_password = hash_password(body.new_password)
    users.revoke_sessions(user)  # sign out every other session...
    session.add(user)
    session.commit()
    session.refresh(user)
    _issue_session(response, user)  # ...but keep this one
    return Message(message="Password changed")


# ----------------------------------------------------------------------------- OIDC (Keycloak)


def _oidc_redirect_uri(request: Request) -> str:
    configured = get_settings().oidc_redirect_url.strip()
    return configured or str(request.url_for("oidc_callback"))


def _to_login(error: str) -> RedirectResponse:
    response = RedirectResponse(f"/login?error={error}", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(OIDC_STATE_COOKIE, path=OIDC_COOKIE_PATH)
    return response


@router.get("/oidc/login", include_in_schema=False)
def oidc_login(request: Request):
    """Start a sign-in at the OpenID Connect provider (AUTH_MODE=oidc)."""
    if get_settings().auth_mode != "oidc":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    login = oidc.new_login_state()
    try:
        target = oidc.authorization_url(login, _oidc_redirect_uri(request))
    except oidc.OidcError:
        logger.exception("OIDC sign-in could not start")
        return _to_login("provider")
    response = RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        OIDC_STATE_COOKIE,
        oidc.encode_login_state(login),
        max_age=oidc.LOGIN_STATE_SECONDS,
        httponly=True,
        secure=get_settings().secure_cookies,
        # Lax: the cookie must come along on the provider's top-level redirect back to us
        samesite="lax",
        path=OIDC_COOKIE_PATH,
    )
    return response


@router.get("/oidc/callback", include_in_schema=False)
def oidc_callback(
    request: Request,
    session: SessionDep,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
):
    """The provider sends the browser back here; on success the app's session cookie is set."""
    if get_settings().auth_mode != "oidc":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    if error:
        # E.g. access_denied when the person cancels at the provider
        logger.info("OIDC provider returned an error: %s", error[:100])
        return _to_login("cancelled" if error == "access_denied" else "provider")
    login = oidc.decode_login_state(request.cookies.get(OIDC_STATE_COOKIE))
    if login is None or not code or not state or not secrets.compare_digest(state, login.state):
        # Expired, opened in another browser, or not started here (login CSRF)
        return _to_login("expired")
    try:
        claims = oidc.sign_in_claims(code, _oidc_redirect_uri(request), login)
    except oidc.OidcError:
        logger.exception("OIDC sign-in failed")
        return _to_login("provider")
    try:
        user = users.sign_in_oidc(session, claims)
    except users.AccountDisabled:
        return _to_login("disabled")
    except users.AccountConflict:
        logger.warning("OIDC sign-in matches an existing account that is not linked to it")
        return _to_login("conflict")
    response = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(OIDC_STATE_COOKIE, path=OIDC_COOKIE_PATH)
    _issue_session(response, user)
    return response
