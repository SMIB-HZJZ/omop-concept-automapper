"""User management and authentication."""

import logging
from datetime import UTC, datetime

from sqlalchemy import func
from sqlmodel import Session, select

from app.core.config import get_settings
from app.core.redis import get_redis
from app.core.security import burn_password_check, hash_password, verify_password
from app.models import AppUser

logger = logging.getLogger(__name__)

ROLES = ("user", "admin")


class LoginLocked(Exception):
    pass


class AccountPending(Exception):
    """Right password, but an admin has not approved the account yet."""


class RegistrationLimited(Exception):
    pass


def get_by_username(session: Session, username: str) -> AppUser | None:
    return session.exec(
        select(AppUser).where(func.lower(AppUser.username) == username.strip().lower())
    ).first()


def create_user(
    session: Session, username: str, password: str, role: str = "user", pending: bool = False
) -> AppUser:
    if role not in ROLES:
        raise ValueError(f"Unknown role {role!r}")
    if get_by_username(session, username):
        raise ValueError(f"User {username!r} already exists")
    user = AppUser(
        username=username.strip(),
        hashed_password=hash_password(password),
        role=role,
        pending=pending,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def _ip_key(username: str, ip: str) -> str:
    return f"login:fail:{username.lower()}:{ip}"


def _user_key(username: str) -> str:
    return f"login:fail:{username.lower()}"


def _failures(key: str) -> int:
    try:
        return int(get_redis().get(key) or 0)
    except Exception:
        logger.warning("Could not read failed-login counter", exc_info=True)
        return 0


def authenticate(session: Session, username: str, password: str, ip: str) -> AppUser | None:
    """Check credentials with two lockouts: per username+IP (LOGIN_MAX_FAILURES) and per username
    across all IPs (LOGIN_MAX_FAILURES_PER_USER), so rotating the client IP does not help."""
    settings = get_settings()
    ip_key, user_key = _ip_key(username, ip), _user_key(username)
    if (
        _failures(ip_key) >= settings.login_max_failures
        or _failures(user_key) >= settings.login_max_failures_per_user
    ):
        raise LoginLocked()

    user = get_by_username(session, username)
    if user is None:
        burn_password_check(password)
    if user is not None and user.pending and user.is_active:
        # Said only to whoever knows the password; a wrong one counts as a failure below
        if verify_password(password, user.hashed_password):
            raise AccountPending()
    if user is None or not user.is_active or not verify_password(password, user.hashed_password):
        try:
            pipe = get_redis().pipeline()
            for key in (ip_key, user_key):
                pipe.incr(key)
                pipe.expire(key, settings.login_lockout_seconds)
            pipe.execute()
        except Exception:
            logger.warning("Could not record failed login", exc_info=True)
        return None

    try:
        get_redis().delete(ip_key, user_key)
    except Exception:
        logger.warning("Could not reset failed-login counters", exc_info=True)
    user.last_login_at = datetime.now(UTC)
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def register(session: Session, email: str, password: str, ip: str) -> AppUser:
    """A self-registered account (role user, email as its username), waiting for approval.

    Limited per client IP and hour, so the form cannot be used to flood the user list."""
    settings = get_settings()
    key = f"register:ip:{ip}"
    try:
        pipe = get_redis().pipeline()
        pipe.incr(key)
        pipe.expire(key, 3600, nx=True)
        count = pipe.execute()[0]
    except Exception:
        logger.warning("Could not count registrations", exc_info=True)
        count = 0
    if count > settings.registrations_per_ip_hour:
        raise RegistrationLimited()
    return create_user(session, email.strip().lower(), password, role="user", pending=True)


def approve(session: Session, user: AppUser) -> AppUser:
    user.pending = False
    user.approved_at = datetime.now(UTC)
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def revoke_sessions(user: AppUser) -> None:
    """Invalidate every token issued to ``user`` (the caller commits)."""
    user.token_version = (user.token_version or 0) + 1


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
    return session.exec(
        select(func.count())
        .select_from(AppUser)
        .where(
            AppUser.role == "admin",
            AppUser.is_active == True,  # noqa: E712
            AppUser.pending == False,  # noqa: E712
        )
    ).one()
