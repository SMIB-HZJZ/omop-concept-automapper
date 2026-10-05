"""User management and authentication."""

import logging
from datetime import UTC, datetime

from sqlalchemy import func
from sqlmodel import Session, select

from app.core.config import get_settings
from app.core.redis import get_redis
from app.core.security import hash_password, verify_password
from app.models import AppUser

logger = logging.getLogger(__name__)

ROLES = ("user", "admin")


class LoginLocked(Exception):
    pass


def get_by_username(session: Session, username: str) -> AppUser | None:
    return session.exec(
        select(AppUser).where(func.lower(AppUser.username) == username.strip().lower())
    ).first()


def create_user(session: Session, username: str, password: str, role: str = "user") -> AppUser:
    if role not in ROLES:
        raise ValueError(f"Unknown role {role!r}")
    if get_by_username(session, username):
        raise ValueError(f"User {username!r} already exists")
    user = AppUser(username=username.strip(), hashed_password=hash_password(password), role=role)
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def _lock_key(username: str, ip: str) -> str:
    return f"login:fail:{username.lower()}:{ip}"


def authenticate(session: Session, username: str, password: str, ip: str) -> AppUser | None:
    settings = get_settings()
    key = _lock_key(username, ip)
    try:
        failures = int(get_redis().get(key) or 0)
    except Exception:
        failures = 0
    if failures >= settings.login_max_failures:
        raise LoginLocked()

    user = get_by_username(session, username)
    if user is None or not user.is_active or not verify_password(password, user.hashed_password):
        try:
            pipe = get_redis().pipeline()
            pipe.incr(key)
            pipe.expire(key, settings.login_lockout_seconds)
            pipe.execute()
        except Exception:
            logger.warning("Could not record failed login", exc_info=True)
        return None

    try:
        get_redis().delete(key)
    except Exception:
        pass
    user.last_login_at = datetime.now(UTC)
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def ensure_bootstrap_admin(session: Session) -> None:
    """Create the initial admin from ADMIN_USERNAME/ADMIN_PASSWORD if no admin exists."""
    settings = get_settings()
    has_admin = session.exec(select(AppUser).where(AppUser.role == "admin")).first()
    if has_admin:
        return
    existing = get_by_username(session, settings.admin_username)
    if existing:
        existing.role = "admin"
        existing.is_active = True
        session.add(existing)
        session.commit()
        logger.info("Promoted existing user %s to admin", existing.username)
        return
    create_user(session, settings.admin_username, settings.admin_password, role="admin")
    logger.info("Created bootstrap admin user %s", settings.admin_username)


def count_admins(session: Session) -> int:
    return len(
        session.exec(
            select(AppUser).where(AppUser.role == "admin", AppUser.is_active == True)  # noqa: E712
        ).all()
    )
