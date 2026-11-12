from fastapi import APIRouter, HTTPException, Request, Response, status

from app.api.deps import CurrentUser, SessionDep
from app.core.config import get_settings
from app.core.security import create_access_token, hash_password, verify_password
from app.models import AppUser
from app.schemas.api import ChangePassword, LoginRequest, LoginResponse, Message, UserOut
from app.services import users

router = APIRouter(prefix="/auth", tags=["auth"])


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
    ip = request.client.host if request.client else "unknown"
    try:
        user = users.authenticate(session, body.username, body.password, ip)
    except users.LoginLocked:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "Too many failed attempts, try again later"
        ) from None
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid username or password")
    token, expires_in = _issue_session(response, user)
    return LoginResponse(
        access_token=token, expires_in=expires_in, user=UserOut.model_validate(user)
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
    if not verify_password(body.current_password, user.hashed_password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect")
    user.hashed_password = hash_password(body.new_password)
    users.revoke_sessions(user)  # sign out every other session...
    session.add(user)
    session.commit()
    session.refresh(user)
    _issue_session(response, user)  # ...but keep this one
    return Message(message="Password changed")
