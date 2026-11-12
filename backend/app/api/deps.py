"""Shared FastAPI dependencies: DB session and the authenticated user."""

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlmodel import Session

from app.core.config import get_settings
from app.core.db import get_session
from app.core.security import decode_access_token
from app.models import AppUser

SessionDep = Annotated[Session, Depends(get_session)]
_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    request: Request,
    session: SessionDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> AppUser:
    token = (
        credentials.credentials if credentials else request.cookies.get(get_settings().cookie_name)
    )
    payload = decode_access_token(token) if token else None
    if not payload or not str(payload.get("sub", "")).isdigit():
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    # Resolved from the DB on every request so deactivation and role changes apply immediately
    user = session.get(AppUser, int(payload["sub"]))
    if user is None or not user.is_active or payload.get("ver") != user.token_version:
        # A version mismatch means the password, role or status changed after the token was issued
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    return user


CurrentUser = Annotated[AppUser, Depends(get_current_user)]


def require_admin(user: CurrentUser) -> AppUser:
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin role required")
    return user


AdminUser = Annotated[AppUser, Depends(require_admin)]
