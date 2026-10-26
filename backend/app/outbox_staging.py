"""Dormant token-and-outbox staging; never called by live auth or delivery.

The caller owns the outer SQLAlchemy transaction. This helper releases or rolls
back a private savepoint and never commits the outer transaction. Only if the
savepoint cannot be safely resolved does it attempt an outer rollback. A
connection failure during the caller's final commit can leave the outcome
uncertain.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import secrets

from sqlalchemy import false, select, text, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models import EmailVerificationOutbox, EmailVerificationToken, User
from app.outbox_token import derive_verification_token


TOKEN_LIFETIME = timedelta(hours=1)


class OutboxStagingError(RuntimeError):
    """Sanitized storage failure; the outer transaction may need rollback."""


@dataclass(frozen=True)
class StagedVerification:
    token_id: int
    job_id: int
    expires_at: datetime


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Outbox timestamps must be timezone-aware")
    return value.astimezone(UTC)


def stage_verification_outbox(*, db: Session, user_id: int, key_version: str,
                              derivation_key: bytes, created_at: datetime,
                              due_at: datetime | None = None,
                              job_expires_at: datetime | None = None) -> StagedVerification:
    """Stage one token and one queued job; return metadata, never a bearer/link.

    Acquire the user lock before any token/outbox lock. An existing user is
    required; this does not consume or rotate older tokens. PostgreSQL uses a
    row lock. SQLite ignores FOR UPDATE, so reserve its sole writer first.
    Flush caller-owned changes before calling: SQLAlchemy's begin_nested()
    otherwise flushes them outside the savepoint. On staging failure, the
    savepoint removes the temporary digest and job while preserving earlier
    outer-transaction work. If savepoint rollback itself fails, a best-effort
    outer rollback is required to prevent a committable placeholder digest.
    Future integrations must recheck cooldown/rotation separately while holding
    the user lock. This helper is not a complete registration/resend workflow.
    """
    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id < 1:
        raise ValueError("Invalid verification user ID")
    created = _utc(created_at)
    token_expiry = created + TOKEN_LIFETIME
    due = created if due_at is None else _utc(due_at)
    job_expiry = token_expiry if job_expires_at is None else _utc(job_expires_at)
    if not created <= due < job_expiry <= token_expiry:
        raise ValueError("Invalid outbox delivery schedule")

    nonce = secrets.token_bytes(32)
    # Validate the dedicated key/version before making any database changes.
    # The validation bearer is short-lived and never assigned or returned.
    derive_verification_token(derivation_key, token_id=1, nonce=nonce,
                              key_version=key_version)
    if db.new or db.dirty or db.deleted:
        raise ValueError("Flush caller changes before outbox staging")

    try:
        if db.get_bind().dialect.name == "sqlite":
            db.execute(text("PRAGMA busy_timeout = 2000"))
            db.execute(update(User).where(false()).values(id=User.id)
                       .execution_options(synchronize_session=False))
        with db.no_autoflush:
            user = db.scalar(select(User).where(User.id == user_id)
                             .with_for_update().execution_options(populate_existing=True))
        if user is None or user.is_active or user.email_verified_at is not None:
            raise ValueError("Verification user is not eligible")
        if db.new or db.dirty or db.deleted:
            raise ValueError("Flush caller changes before outbox staging")

        savepoint = db.begin_nested()
        try:
            staged = _stage_pair(db, user, nonce, derivation_key, key_version,
                                 created, due, job_expiry, token_expiry)
        except BaseException:
            if savepoint.is_active:
                try:
                    savepoint.rollback()
                except Exception:
                    # A failed savepoint rollback cannot establish isolation.
                    # Sacrifice outer work rather than allow a placeholder commit.
                    try:
                        db.rollback()
                    except Exception:
                        pass  # Caller must discard a session with uncertain state.
            raise
        try:
            savepoint.commit()
        except BaseException:
            # A failed RELEASE may have an uncertain savepoint outcome. Do not
            # leave the outer transaction available for a later commit.
            try:
                db.rollback()
            except Exception:
                pass
            raise
        return staged
    except SQLAlchemyError:
        raise OutboxStagingError("Outbox storage unavailable") from None


def _stage_pair(db: Session, user: User, nonce: bytes, derivation_key: bytes,
                key_version: str, created: datetime, due: datetime,
                job_expiry: datetime, token_expiry: datetime) -> StagedVerification:
    """Write both rows within the caller's private savepoint."""
    # The ID is needed for derivation. The placeholder is confined to this
    # savepoint and replaced before it is released to the outer transaction.
    token = EmailVerificationToken(
        user_id=user.id, token_hash=hashlib.sha256(secrets.token_bytes(32)).digest(),
        created_at=created, expires_at=token_expiry,
    )
    db.add(token)
    db.flush()
    token.token_hash = hashlib.sha256(derive_verification_token(
        derivation_key, token_id=token.id, nonce=nonce,
        key_version=key_version,
    ).encode("ascii")).digest()
    job = EmailVerificationOutbox(
        token_id=token.id, nonce=nonce, key_version=key_version,
        state="queued", created_at=created, next_attempt_at=due,
        expires_at=job_expiry,
    )
    db.add(job)
    db.flush()
    return StagedVerification(token.id, job.id, token_expiry)
