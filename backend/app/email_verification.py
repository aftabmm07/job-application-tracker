"""Secure email verification token utilities."""

import hashlib
import re
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import false, select, text, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models import EmailVerificationToken, User
from app.auth_config import AuthSettings
from app.email_delivery import EmailSender


RESEND_COOLDOWN = timedelta(minutes=10)

@dataclass(frozen=True)
class IssuedVerificationLink:
    user_id: int
    expires_at: datetime
    link: str | None = field(repr=False)


def generate_verification_token() -> tuple[str, bytes]:
    """Generate a random token and its SHA-256 digest."""

    raw_token = secrets.token_urlsafe(32)

    token_hash = hashlib.sha256(
        raw_token.encode("ascii")
    ).digest()

    return raw_token, token_hash


def is_verification_token_valid(
    *,
    expires_at: datetime,
    consumed_at: datetime | None,
    now: datetime,
) -> bool:
    """Check whether a verification token is unexpired and unused."""

    if consumed_at is not None:
        return False

    if expires_at.tzinfo is None or now.tzinfo is None:
        return False

    return now.astimezone(UTC) < expires_at.astimezone(UTC)


class VerificationStorageError(RuntimeError):
    """Sanitized storage failure, containing no submitted credentials."""


def utc_now():
    return datetime.now(UTC)


def _stage_verification_token(db: Session, user_id: int, *, settings: AuthSettings | None,
                              created_at: datetime) -> IssuedVerificationLink:
    """Store only the digest; keep the bearer in a short-lived, repr-hidden value."""
    raw_token, digest = generate_verification_token()
    expires_at = created_at + timedelta(hours=1)
    db.add(EmailVerificationToken(user_id=user_id, token_hash=digest,
                                  created_at=created_at,
                                  expires_at=expires_at))
    origin = settings.verification_frontend_origin if settings is not None else None
    link = origin + "/verify-email#token=" + raw_token if origin else None
    return IssuedVerificationLink(user_id, expires_at, link)


def add_registration_verification_token(db: Session, user_id: int, *, settings: AuthSettings | None = None):
    """Stage a one-hour token in the new user's transaction; caller commits."""
    return _stage_verification_token(db, user_id, settings=settings, created_at=utc_now())


def _storage_failure(db: Session):
    try:
        db.rollback()
    except SQLAlchemyError:
        pass
    raise VerificationStorageError("Email verification storage is unavailable") from None


def _rollback_verification(db: Session):
    try:
        db.rollback()
    except SQLAlchemyError:
        _storage_failure(db)


def _prepare_verification_transaction(db: Session):
    """Bound waits and reserve SQLite's sole writer before reading mutable rows.

    SQLite ignores FOR UPDATE, so the no-op UPDATE provides database-wide writer
    serialization. PostgreSQL uses real row locks in the callers, always user
    before token rows. Its lock/statement timeouts are transaction-local.
    """
    dialect = db.get_bind().dialect.name
    if dialect == "sqlite":
        db.execute(text("PRAGMA busy_timeout = 2000"))
        db.execute(update(EmailVerificationToken).where(false()).values(
            id=EmailVerificationToken.id,
        ).execution_options(synchronize_session=False))
    elif dialect == "postgresql":
        db.execute(text("SELECT set_config('lock_timeout', '2000ms', true), "
                        "set_config('statement_timeout', '5000ms', true)"))


def commit_verification_changes(db: Session):
    """Commit before reporting success; rollback best-effort on storage errors.

    A lost connection during commit can leave the database outcome unknown even
    after rollback is attempted. Never translate that exception into success or
    automatically retry the transaction. Dispose of the failed session normally.
    """
    try:
        db.commit()
    except SQLAlchemyError:
        _storage_failure(db)


def activate_and_commit_verified_user(*, db: Session, raw_token: str) -> bool:
    """Endpoint boundary: True only after commit returns successfully.

    Use a dedicated Session/transaction. Map VerificationStorageError to a generic
    unavailable response without logging exception locals. Invalid attempts roll
    back to release locks. This helper neither issues sessions nor returns tokens.
    """
    if not activate_verified_user(db=db, raw_token=raw_token):
        _rollback_verification(db)
        return False
    commit_verification_changes(db)
    return True


def activate_verified_user(*, db: Session, raw_token: str, now: datetime | None = None) -> bool:
    """Activate once; caller MUST commit before reporting success.

    Use a dedicated transaction. False means invalid, expired, consumed, or an
    already verified account. No sessions are issued. ``now`` is for deterministic
    tests only; normal callers sample wall-clock time after acquiring both locks.
    PostgreSQL locks the user row before the token row. SQLite's FOR UPDATE is
    ineffective; its writer reservation serializes the full operation instead.
    Database failures roll back the entire transaction and raise a safe error.
    """
    if not isinstance(raw_token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", raw_token):
        return False
    if now is not None and (now.tzinfo is None or now.utcoffset() is None):
        raise ValueError("Verification time must be timezone-aware")
    digest = hashlib.sha256(raw_token.encode("ascii")).digest()
    try:
        with db.no_autoflush:
            _prepare_verification_transaction(db)
            # This read only locates the lock key; do not trust its token state.
            owner_id = db.scalar(select(EmailVerificationToken.user_id).where(
                EmailVerificationToken.token_hash == digest,
            ))
            if owner_id is None:
                return False
            user = db.scalar(select(User).where(User.id == owner_id).with_for_update()
                             .execution_options(populate_existing=True))
            if user is None:
                return False
            token = db.scalar(select(EmailVerificationToken).where(
                EmailVerificationToken.token_hash == digest,
                EmailVerificationToken.user_id == owner_id,
            ).with_for_update().execution_options(populate_existing=True))
            checked_at = (now if now is not None else utc_now()).astimezone(UTC)
            if (token is None or user.is_active or user.email_verified_at is not None
                    or token.created_at > checked_at
                    or not is_verification_token_valid(expires_at=token.expires_at,
                                                       consumed_at=token.consumed_at, now=checked_at)):
                return False
            token.consumed_at = checked_at
            user.email_verified_at = checked_at
            user.is_active = True
        db.flush()
        return True
    except SQLAlchemyError:
        _storage_failure(db)


def resend_verification_for_user(*, db: Session, user_id: int, sender: EmailSender,
                                 settings: AuthSettings, cooldown: timedelta = RESEND_COOLDOWN) -> bool:
    """Rotate an inactive user's token and deliver it; no HTTP response is made.

    True means the sender returned and commit completed. False means ineligible,
    within cooldown, or delivery failed. Storage/commit errors raise a sanitized
    error. A sender may accept mail before an uncertain commit: never infer from
    an exception that the recipient did or did not receive a usable link.
    """
    if settings.verification_frontend_origin is None or sender is None:
        raise ValueError("Verification delivery is not configured")
    if not timedelta(0) < cooldown <= timedelta(hours=1):
        raise ValueError("Invalid verification cooldown")
    try:
        with db.no_autoflush:
            _prepare_verification_transaction(db)
            user = db.scalar(select(User).where(User.id == user_id).with_for_update()
                             .execution_options(populate_existing=True))
            # Sample time only after the user lock; never use transaction-start now().
            checked_at = utc_now()
            if user is None or user.is_active or user.email_verified_at is not None:
                _rollback_verification(db)
                return False
            latest = db.scalar(select(EmailVerificationToken.created_at).where(
                EmailVerificationToken.user_id == user_id,
            ).order_by(EmailVerificationToken.created_at.desc(), EmailVerificationToken.id.desc()).limit(1))
            if latest is not None and checked_at < latest + cooldown:
                _rollback_verification(db)
                return False
            db.execute(update(EmailVerificationToken).where(
                EmailVerificationToken.user_id == user_id,
                EmailVerificationToken.consumed_at.is_(None),
            ).values(consumed_at=checked_at).execution_options(synchronize_session=False))
            issued = _stage_verification_token(db, user_id, settings=settings, created_at=checked_at)
        db.flush()
    except SQLAlchemyError:
        _storage_failure(db)
    try:
        sender.send_verification(user.email, issued.link, issued.expires_at)
    except Exception:
        # Keep the previous token usable when the database rollback succeeds.
        _rollback_verification(db)
        return False
    commit_verification_changes(db)
    return True
