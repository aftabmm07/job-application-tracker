"""Dormant transaction staging for hypothetical verification-outbox flows.

No route or worker imports this module. Each function uses the caller's Session
and NEVER commits or rolls back its outer transaction. The caller must roll back
on an exception or declined operation, then commit exactly once on success.

PostgreSQL lock order is user, token IDs ascending, then outbox jobs. The
initial token-hash lookup in activation only locates a user ID; it is not an
authorization decision. All mutable state is rechecked under row locks. SQLite
ignores FOR UPDATE; its writer reservation serializes offline tests but does
not prove PostgreSQL row-lock, deadlock, or worker behavior.

Relevant tokens are locked in bounded keyset pages before any outbox writes.
The outer transaction retains those locks across both passes; this bounds
memory, not the total transaction duration or number of rows locked.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
import hashlib
import hmac
import re

from sqlalchemy import exists, or_, select
from sqlalchemy.orm import Session

from app.email_verification import (
    RESEND_COOLDOWN, _prepare_verification_transaction,
    is_verification_token_valid, utc_now,
)
from app.models import EmailVerificationOutbox, EmailVerificationToken, User
from app.outbox_persistence import cancel_consumed_token_job
from app.outbox_staging import StagedVerification, stage_verification_outbox


Clock = Callable[[], datetime]
DEFAULT_BATCH_SIZE = 100
MAX_BATCH_SIZE = 500


def _time(clock: Clock) -> datetime:
    value = clock()
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Verification time must be timezone-aware")
    return value.astimezone(UTC)


def _batch_size(value: int) -> int:
    if type(value) is not int or not 1 <= value <= MAX_BATCH_SIZE:
        raise ValueError("Invalid verification token batch size")
    return value


def _relevant(user_id: int, after_id: int, selected_id: int | None = None):
    """Include unused tokens AND any consumed/expired token with a live job."""
    live_job = exists(select(EmailVerificationOutbox.id).where(
        EmailVerificationOutbox.token_id == EmailVerificationToken.id,
        EmailVerificationOutbox.state.in_(("queued", "leased")),
    ))
    return (EmailVerificationToken.user_id == user_id,
            EmailVerificationToken.id > after_id,
            or_(EmailVerificationToken.consumed_at.is_(None), live_job,
                EmailVerificationToken.id == selected_id) if selected_id is not None
            else or_(EmailVerificationToken.consumed_at.is_(None), live_job))


def _lock_relevant_tokens(db: Session, user_id: int, batch_size: int,
                          selected_id: int | None = None) -> None:
    """Lock all relevant token rows before taking ANY outbox row lock.

    Cooperating issuers must hold the same user lock before inserting tokens.
    Workers may change a live job to terminal while this scan runs; that makes
    cancellation unnecessary. Terminal jobs cannot become claimable again.
    """
    cursor = 0
    while True:
        ids = db.scalars(select(EmailVerificationToken.id).where(
            *_relevant(user_id, cursor, selected_id),
        ).order_by(EmailVerificationToken.id).limit(batch_size)
            .with_for_update(of=EmailVerificationToken)).all()
        if not ids:
            return
        cursor = ids[-1]


def _consume_and_cancel_batches(db: Session, user_id: int, checked_at: datetime,
                                batch_size: int, selected_id: int | None = None) -> None:
    cursor = 0
    while True:
        tokens = db.scalars(select(EmailVerificationToken).where(
            *_relevant(user_id, cursor, selected_id),
        ).order_by(EmailVerificationToken.id).limit(batch_size)
            .execution_options(populate_existing=True)).all()
        if not tokens:
            return
        cursor = tokens[-1].id  # Keyset cursor survives eligibility changes.
        for token in tokens:
            if token.consumed_at is None:
                token.consumed_at = checked_at
        db.flush()  # Cancellation's SQL predicate requires consumed_at.
        for token in tokens:
            cancel_consumed_token_job(db=db, user_id=user_id, token_id=token.id)


def stage_registration(*, db: Session, email: str, password_hash: str,
                       key_version: str, derivation_key: bytes,
                       clock: Clock = utc_now) -> tuple[int, StagedVerification]:
    """Insert an inactive user and one pair; caller commits once.

    Inputs must already satisfy registration validation and password hashing.
    The database's normalized-email uniqueness remains authoritative. A failed
    flush requires caller rollback; never report an account as created first.
    """
    if db.new or db.dirty or db.deleted:
        raise ValueError("Flush caller changes before outbox registration")
    user = User(email=email, password_hash=password_hash, is_active=False)
    db.add(user)
    db.flush()  # Obtain ID and check uniqueness before staging its pair.
    staged = stage_verification_outbox(db=db, user_id=user.id,
                                      key_version=key_version,
                                      derivation_key=derivation_key,
                                      created_at=_time(clock))
    return user.id, staged


def stage_resend(*, db: Session, user_id: int, key_version: str,
                 derivation_key: bytes, clock: Clock = utc_now,
                 cooldown: timedelta = RESEND_COOLDOWN,
                 batch_size: int = DEFAULT_BATCH_SIZE) -> StagedVerification | None:
    """Rotate tokens and jobs for an eligible user; None means no change.

    Lock user first, then relevant tokens in ascending ID order. Check cooldown
    under the user lock before scanning history; sample time again after token
    locks. None leaves a transaction for the caller to roll back.
    """
    if not isinstance(cooldown, timedelta) or not timedelta(0) < cooldown <= timedelta(hours=1):
        raise ValueError("Invalid verification cooldown")
    _batch_size(batch_size)
    if db.new or db.dirty or db.deleted:
        raise ValueError("Flush caller changes before outbox resend")
    with db.no_autoflush:
        _prepare_verification_transaction(db)
        user = db.scalar(select(User).where(User.id == user_id).with_for_update()
                         .execution_options(populate_existing=True))
        if user is None or user.is_active or user.email_verified_at is not None:
            return None
        latest = db.scalar(select(EmailVerificationToken.created_at).where(
            EmailVerificationToken.user_id == user_id,
        ).order_by(EmailVerificationToken.created_at.desc(),
                   EmailVerificationToken.id.desc()).limit(1))
        if latest is not None and _time(clock) < latest + cooldown:
            return None
        _lock_relevant_tokens(db, user_id, batch_size)
        checked_at = _time(clock)
        _consume_and_cancel_batches(db, user_id, checked_at, batch_size)
        return stage_verification_outbox(db=db, user_id=user_id,
                                         key_version=key_version,
                                         derivation_key=derivation_key,
                                         created_at=checked_at)


def stage_activation(*, db: Session, raw_token: str,
                     clock: Clock = utc_now,
                     batch_size: int = DEFAULT_BATCH_SIZE) -> bool:
    """Activate once without committing; caller supplies a transient bearer.

    Never return, log, or store the bearer. A false result makes no changes.
    The caller must roll back to release locks even after a false result.
    """
    if not isinstance(raw_token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", raw_token):
        return False
    _batch_size(batch_size)
    if db.new or db.dirty or db.deleted:
        raise ValueError("Flush caller changes before outbox activation")
    digest = hashlib.sha256(raw_token.encode("ascii")).digest()
    with db.no_autoflush:
        _prepare_verification_transaction(db)
        # Locate only; the row is re-read after locking its user.
        locator = db.execute(select(EmailVerificationToken.user_id,
                                    EmailVerificationToken.id).where(
            EmailVerificationToken.token_hash == digest,
        )).one_or_none()
        if locator is None:
            return False
        user_id, selected_id = locator
        user = db.scalar(select(User).where(User.id == user_id).with_for_update()
                         .execution_options(populate_existing=True))
        if user is None or user.is_active or user.email_verified_at is not None:
            return False
        _lock_relevant_tokens(db, user_id, batch_size, selected_id)
        token = db.scalar(select(EmailVerificationToken).where(
            EmailVerificationToken.id == selected_id,
            EmailVerificationToken.user_id == user_id,
        ).execution_options(populate_existing=True))
        checked_at = _time(clock)
        if (token is None or not hmac.compare_digest(token.token_hash, digest)
                or token.created_at > checked_at
                or not is_verification_token_valid(expires_at=token.expires_at,
                                                   consumed_at=token.consumed_at,
                                                   now=checked_at)):
            return False
        _consume_and_cancel_batches(db, user_id, checked_at, batch_size, selected_id)
        user.email_verified_at = checked_at
        user.is_active = True
        db.flush()
        return True
