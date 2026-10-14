"""Database-backed sessions. Callers commit BEFORE sending a token cookie."""
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import hashlib
import re
import secrets

from sqlalchemy import case, delete, exists, or_, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.auth_config import AuthSettings
from app.models import AuthSession, User

ANONYMOUS_MAX_AGE_SECONDS = 900
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}")


class SessionStorageError(RuntimeError):
    """Sanitized storage failure; future endpoints should return generic 503."""


class InvalidSessionError(ValueError):
    """Invalid session or ineligible user; contains no credentials."""


def token_digest(raw_token: str | None) -> bytes | None:
    if not isinstance(raw_token, str) or not _TOKEN_PATTERN.fullmatch(raw_token):
        return None
    return hashlib.sha256(raw_token.encode("ascii")).digest()


def utc_now():
    return datetime.now(UTC)


def _storage_failure(db):
    try:
        db.rollback()
    except SQLAlchemyError:
        pass
    raise SessionStorageError("Session storage is unavailable") from None


def commit_session_changes(db: Session):
    """Endpoint boundary: rollback/sanitize commit failures before setting cookies."""
    try:
        db.commit()
    except SQLAlchemyError:
        _storage_failure(db)


@dataclass(frozen=True)
class IssuedSession:
    session_id: int
    expires_at: datetime
    raw_token: str = field(repr=False)


class SessionService:
    def __init__(self, db: Session, settings: AuthSettings, clock=utc_now):
        self.db = db
        self.settings = settings
        self.clock = clock

    def _now(self):
        now = self.clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Session clock must be timezone-aware")
        return now.astimezone(UTC)

    def _conditions(self, digest, now):
        eligible_user = exists().where(
            User.id == AuthSession.user_id, User.is_active.is_(True),
            User.email_verified_at.is_not(None),
        )
        return (
            AuthSession.token_hash == digest,
            AuthSession.created_at <= now,
            AuthSession.last_activity_at <= now,
            AuthSession.expires_at > now,
            AuthSession.idle_expires_at > now,
            or_(AuthSession.user_id.is_(None), eligible_user),
        )

    def _insert(self, user_id, now):
        if user_id is not None:
            user = self.db.scalar(select(User.id).where(
                User.id == user_id, User.is_active.is_(True), User.email_verified_at.is_not(None),
            ))
            if user is None:
                raise InvalidSessionError("An active verified user is required")
        lifetime = self.settings.session_absolute_timeout_seconds
        if user_id is None:
            lifetime = min(lifetime, ANONYMOUS_MAX_AGE_SECONDS)
        expires_at = now + timedelta(seconds=lifetime)
        raw_token = secrets.token_urlsafe(32)  # 256 random bits; never attach to ORM objects.
        record = AuthSession(
            token_hash=token_digest(raw_token), user_id=user_id, created_at=now,
            expires_at=expires_at, last_activity_at=now,
            idle_expires_at=min(expires_at, now + timedelta(seconds=self.settings.session_idle_timeout_seconds)),
        )
        self.db.add(record)
        self.db.flush()
        return IssuedSession(record.id, expires_at, raw_token)

    def create(self, user_id: int | None = None) -> IssuedSession:
        try:
            return self._insert(user_id, self._now())
        except SQLAlchemyError:
            _storage_failure(self.db)

    def lookup(self, raw_token: str | None) -> AuthSession | None:
        """Read-only validation; never renew an invalid or failed-CSRF request."""
        digest = token_digest(raw_token)
        if digest is None:
            return None
        try:
            return self.db.scalar(select(AuthSession).where(
                *self._conditions(digest, self._now()),
            ).execution_options(populate_existing=True))
        except SQLAlchemyError:
            _storage_failure(self.db)

    def renew(self, raw_token: str | None) -> AuthSession | None:
        """Conditional UPDATE cannot recreate a revoked row or revive an expired one."""
        digest = token_digest(raw_token)
        if digest is None:
            return None
        now = self._now()
        idle_deadline = now + timedelta(seconds=self.settings.session_idle_timeout_seconds)
        try:
            return self.db.scalar(update(AuthSession).where(
                *self._conditions(digest, now),
            ).values(
                last_activity_at=now,
                idle_expires_at=case((AuthSession.expires_at < idle_deadline, AuthSession.expires_at), else_=idle_deadline),
            ).returning(AuthSession).execution_options(populate_existing=True))
        except SQLAlchemyError:
            _storage_failure(self.db)

    def revoke(self, raw_token: str | None) -> bool:
        digest = token_digest(raw_token)
        if digest is None:
            return False
        try:
            result = self.db.execute(delete(AuthSession).where(AuthSession.token_hash == digest))
            return result.rowcount > 0
        except SQLAlchemyError:
            _storage_failure(self.db)

    def rotate_for_login(self, anonymous_token: str, user_id: int) -> IssuedSession:
        """Caller has verified credentials/CSRF. Delete+insert require ONE final commit."""
        digest = token_digest(anonymous_token)
        if digest is None:
            raise InvalidSessionError("A valid anonymous session is required")
        now = self._now()
        try:
            # PostgreSQL locks the old row until commit, preventing parallel logins
            # from consuming the same anonymous session more than once.
            previous = self.db.scalar(select(AuthSession).where(
                *self._conditions(digest, now), AuthSession.user_id.is_(None),
            ).with_for_update().execution_options(populate_existing=True))
            if previous is None:
                raise InvalidSessionError("A valid anonymous session is required")
            issued = self._insert(user_id, now)
            self.db.execute(delete(AuthSession).where(AuthSession.id == previous.id))
            self.db.flush()
            return issued
        except SQLAlchemyError:
            _storage_failure(self.db)
