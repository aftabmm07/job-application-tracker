"""Dormant, single-job outbox writes. No polling, delivery, or hidden commits.

Call each function in a caller-owned SQLAlchemy transaction and commit before
acting on its result. On any exception, the caller must roll back. These writes
fence outbox rows only: they do not settle races with user/token invalidation.
No PostgreSQL concurrent-worker behavior is established by SQLite tests.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
import secrets

from sqlalchemy import and_, case, exists, or_, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models import EmailVerificationOutbox as Job, EmailVerificationToken as Token


MAX_LEASE_SECONDS = 60


class OutboxClaimLost(RuntimeError):
    """No row matched the atomic predicate; caller must not report success."""


class OutboxStorageError(RuntimeError):
    """Sanitized database failure; caller must roll back the transaction."""


@dataclass(frozen=True)
class ClaimedLease:
    job_id: int
    generation: int
    expires_at: datetime
    owner: bytes = field(repr=False)


class CancellationOutcome(str, Enum):
    CANCELLED = "cancelled"  # This call staged a new cancellation.
    ALREADY_CANCELLED = "already_cancelled"
    TERMINAL = "terminal"  # Delivered or uncertain; never rewritten.
    NOT_ELIGIBLE = "not_eligible"  # No owned job or token is not consumed.


def _time(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Outbox time must be timezone-aware")
    return value.astimezone(UTC)


def _limit(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("Outbox maximum attempts must be positive")
    return value


def _id(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("Outbox job ID must be positive")
    return value


def _lease_predicates(*, job_id: int, owner: bytes, generation: int,
                      now: datetime, max_attempts: int):
    _id(job_id)
    limit = _limit(max_attempts)
    if not isinstance(owner, bytes) or len(owner) != 16:
        raise ValueError("Invalid outbox lease owner")
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
        raise ValueError("Invalid outbox fencing generation")
    checked_at = _time(now)
    return (
        Job.id == job_id,
        Job.state == "leased",
        Job.lease_owner == owner,
        Job.fencing_generation == generation,
        Job.lease_expires_at > checked_at,
        Job.expires_at > checked_at,
        Job.created_at <= checked_at,
        Job.attempts >= 1,
        Job.attempts <= limit,
    )


def _one(db: Session, statement):
    try:
        row = db.execute(statement.execution_options(synchronize_session=False)).one_or_none()
    except SQLAlchemyError:
        raise OutboxStorageError("Outbox storage unavailable") from None
    if row is None:
        raise OutboxClaimLost("Outbox claim is no longer valid")
    return row


def claim_job(*, db: Session, job_id: int, now: datetime,
              max_attempts: int, lease_duration: timedelta) -> ClaimedLease:
    """Stage a claim/reclaim; caller commits before using the returned lease.

    ``now`` must be sampled immediately before this call. A future PostgreSQL
    worker must revisit wall-clock checks after lock waits and prove concurrency
    behavior separately; a caller-supplied timestamp alone cannot do that.
    """
    _id(job_id)
    limit = _limit(max_attempts)
    checked_at = _time(now)
    if not isinstance(lease_duration, timedelta) or not timedelta(0) < lease_duration <= timedelta(seconds=MAX_LEASE_SECONDS):
        raise ValueError("Outbox lease duration is invalid")
    requested_end = checked_at + lease_duration
    owner = secrets.token_bytes(16)
    available = or_(
        and_(Job.state == "queued", Job.lease_owner.is_(None), Job.lease_expires_at.is_(None)),
        and_(Job.state == "leased", Job.lease_owner.is_not(None),
             Job.lease_expires_at <= checked_at, Job.fencing_generation > 0),
    )
    statement = (update(Job).where(
        Job.id == job_id, available,
        Job.created_at <= checked_at,
        Job.next_attempt_at <= checked_at,
        Job.expires_at > checked_at,
        Job.attempts >= 0,
        Job.attempts < limit,
        Job.fencing_generation >= 0,
    ).values(
        state="leased", attempts=Job.attempts + 1,
        fencing_generation=Job.fencing_generation + 1,
        lease_owner=owner,
        lease_expires_at=case((Job.expires_at < requested_end, Job.expires_at), else_=requested_end),
        failure_category=None,
    ).returning(Job.id, Job.fencing_generation, Job.lease_expires_at))
    row = _one(db, statement)
    return ClaimedLease(row[0], row[1], row[2], owner)


def mark_delivered(*, db: Session, job_id: int, owner: bytes, generation: int,
                   now: datetime, max_attempts: int) -> None:
    """Stage SMTP-accepted state, never an inbox-delivery assertion."""
    conditions = _lease_predicates(job_id=job_id, owner=owner, generation=generation,
                                   now=now, max_attempts=max_attempts)
    _one(db, update(Job).where(*conditions).values(
        state="delivered", lease_owner=None, lease_expires_at=None,
        failure_category=None,
    ).returning(Job.id))


def schedule_retry(*, db: Session, job_id: int, owner: bytes, generation: int,
                   now: datetime, max_attempts: int, retry_at: datetime) -> None:
    """Stage retry only after a definite pre-acceptance transport failure.

    SMTP timeout or uncertain acceptance must use ``mark_uncertain`` instead.
    """
    conditions = _lease_predicates(job_id=job_id, owner=owner, generation=generation,
                                   now=now, max_attempts=max_attempts)
    checked_at, scheduled = _time(now), _time(retry_at)
    if scheduled < checked_at:
        raise ValueError("Outbox retry time is invalid")
    _one(db, update(Job).where(*conditions, Job.attempts < max_attempts,
                                Job.expires_at > scheduled).values(
        state="queued", next_attempt_at=scheduled,
        lease_owner=None, lease_expires_at=None,
        failure_category="transport",
    ).returning(Job.id))


def mark_uncertain(*, db: Session, job_id: int, owner: bytes, generation: int,
                   now: datetime, max_attempts: int) -> None:
    """Stage a terminal-for-automatic-retries uncertainty outcome."""
    conditions = _lease_predicates(job_id=job_id, owner=owner, generation=generation,
                                   now=now, max_attempts=max_attempts)
    _one(db, update(Job).where(*conditions).values(
        state="uncertain", lease_owner=None, lease_expires_at=None,
        failure_category="uncertain",
    ).returning(Job.id))


def cancel_consumed_token_job(*, db: Session, user_id: int,
                              token_id: int) -> CancellationOutcome:
    """Stage cancellation after token consumption; never commit or roll back.

    Caller owns one transaction and must first lock the user, then the token,
    verify eligibility, set ``consumed_at``, and flush that change. Only then
    call this helper, which writes/locks the outbox last. For multiple tokens,
    lock tokens in a stable order before cancelling their jobs. On an exception,
    the caller must roll back. Commit token consumption and cancellation together.

    Clearing a live lease and incrementing its fence prevents stale database
    completion, retry, or uncertain-state writes. It cannot retract an SMTP
    message already accepted by a remote server.
    """
    _id(user_id)
    _id(token_id)
    owned_and_consumed = exists(select(Token.id).where(
        Token.id == Job.token_id, Token.user_id == user_id,
        Token.consumed_at.is_not(None),
    ))
    statement = (update(Job).where(
        Job.token_id == token_id,
        Job.state.in_(("queued", "leased")),
        owned_and_consumed,
    ).values(
        state="cancelled", lease_owner=None, lease_expires_at=None,
        fencing_generation=Job.fencing_generation + 1,
        failure_category=None,
    ).returning(Job.id).execution_options(synchronize_session=False))
    try:
        changed = db.execute(statement).scalar_one_or_none()
        if changed is not None:
            return CancellationOutcome.CANCELLED
        # A no-op is an explicit outcome, not a successful cancellation. The
        # caller's user/token locks make this classification stable on PG.
        row = db.execute(select(Job.state, Token.consumed_at).join(
            Token, Token.id == Job.token_id,
        ).where(Job.token_id == token_id, Token.user_id == user_id)
            .with_for_update(of=Job)).one_or_none()
    except SQLAlchemyError:
        raise OutboxStorageError("Outbox storage unavailable") from None
    if row is None:
        return CancellationOutcome.NOT_ELIGIBLE
    state, consumed_at = row
    if state == "cancelled":
        return CancellationOutcome.ALREADY_CANCELLED
    if state in {"delivered", "uncertain"}:
        return CancellationOutcome.TERMINAL
    if consumed_at is None:
        return CancellationOutcome.NOT_ELIGIBLE
    # A consumed token with a live job should have matched. Do not conceal a
    # violated lock-order/transaction contract as an idempotent no-op.
    raise OutboxClaimLost("Outbox cancellation lost a concurrent race")
