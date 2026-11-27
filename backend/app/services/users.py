"""User management and authentication."""

import logging
from datetime import UTC, datetime

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
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


class AccountDisabled(Exception):
    """The account was deactivated by an admin."""


class AccountConflict(Exception):
    """An account with the provider's email exists but belongs to someone else (not linked)."""


# Stored as the password hash of accounts that sign in through OIDC: matches no password
NO_PASSWORD = "!oidc"


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


UNVERIFIED_PREFIX = "unverified:"


def sign_in_oidc(session: Session, claims: dict, _retry: bool = True) -> AppUser:
    """The account for a verified OIDC ID token, created on the first sign-in.

    Every account is approved at once with the role user: who may sign in at all is decided in
    the provider. Accounts are found by the provider's user id (sub), so a changed email does
    not create a second account.

    The email names the account only when the provider marks it as verified; otherwise the name
    is ``unverified:<provider username>``, so nobody can take a colleague's email (and lock them
    out, or pose as them in the mapping history). Once the email is verified and free, the
    account is renamed to it. The person whose verified email is OIDC_INITIAL_ADMIN becomes an
    admin on the first sign-in, or on a later one while there is no active admin (e.g. after
    the email was verified).

    Raises AccountDisabled or AccountConflict when no session may be issued."""
    settings = get_settings()
    subject = str(claims["sub"])
    email = str(claims.get("email") or "").strip().lower()
    # A JSON boolean in Keycloak; some providers send the string "true"
    verified = bool(email) and claims.get("email_verified") in (True, "true")
    initial = settings.oidc_initial_admin.strip().lower()
    is_initial_admin = bool(initial) and email == initial and verified
    if initial and email == initial and not verified:
        logger.warning(
            "OIDC_INITIAL_ADMIN signed in, but the provider does not mark the email as verified;"
            " the account gets the role user until it is (mark the email verified in the"
            " provider, then sign in again)"
        )
    now = datetime.now(UTC)
    user = session.exec(select(AppUser).where(AppUser.oidc_subject == subject)).first()
    if user is None:
        if verified:
            username = email
        else:
            username = UNVERIFIED_PREFIX + str(claims.get("preferred_username") or subject)
        if get_by_username(session, username):
            raise AccountConflict(username)
        user = AppUser(
            username=username,
            hashed_password=NO_PASSWORD,
            oidc_subject=subject,
            role="admin" if is_initial_admin else "user",
            approved_at=now,
        )
        logger.info(
            "Created %s account %s on first OIDC sign-in",
            "admin" if is_initial_admin else "user",
            username,
        )
    else:
        if verified and user.username.startswith(UNVERIFIED_PREFIX):
            if get_by_username(session, email) is None:
                user.username = email
        if is_initial_admin and user.role != "admin" and count_admins(session, oidc=True) == 0:
            user.role = "admin"
            revoke_sessions(user)
            logger.info("Promoted OIDC_INITIAL_ADMIN %s: there was no active admin", email)
    if user.pending:
        # Created while OIDC accounts still waited for approval
        user.pending = False
        user.approved_at = now
    if not user.is_active:
        raise AccountDisabled(user.username)
    user.last_login_at = now
    session.add(user)
    try:
        session.commit()
    except IntegrityError:
        # The same person's first sign-in in two windows at once: the other one created it
        session.rollback()
        if not _retry:
            raise
        return sign_in_oidc(session, claims, _retry=False)
    session.refresh(user)
    return user


def make_admin(session: Session, username: str) -> AppUser:
    """Approve, activate and promote an account (recovery from the server's command line)."""
    user = get_by_username(session, username)
    if user is None:
        raise ValueError(f"No user {username!r}")
    user.role = "admin"
    user.is_active = True
    if user.pending:
        user.pending = False
        user.approved_at = datetime.now(UTC)
    revoke_sessions(user)
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def revoke_sessions(user: AppUser) -> None:
    """Invalidate every token issued to ``user`` (the caller commits)."""
    user.token_version = (user.token_version or 0) + 1


def ensure_bootstrap_admin(session: Session) -> None:
    """Create the initial admin from ADMIN_USERNAME/ADMIN_PASSWORD if no admin exists.

    Not with AUTH_MODE=oidc: there the first admin is OIDC_INITIAL_ADMIN (see sign_in_oidc)."""
    settings = get_settings()
    if settings.auth_mode == "oidc":
        return
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


def count_admins(session: Session, oidc: bool = False) -> int:
    """Active, approved admins; with ``oidc`` only those who sign in through the provider (a
    password admin from before a switch to AUTH_MODE=oidc cannot sign in)."""
    conditions = [
        AppUser.role == "admin",
        AppUser.is_active == True,  # noqa: E712
        AppUser.pending == False,  # noqa: E712
    ]
    if oidc:
        conditions.append(AppUser.oidc_subject.is_not(None))  # type: ignore[union-attr]
    return session.exec(select(func.count()).select_from(AppUser).where(*conditions)).one()
