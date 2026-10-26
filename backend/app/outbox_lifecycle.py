"""Pure policy for the dormant verification outbox; no database or delivery work.

These checks are advisory snapshots. A future worker must enforce the same
predicates in atomic conditional database writes and verify affected row counts.
``delivered`` means SMTP accepted a message, not that an inbox received it.
"""

from datetime import UTC, datetime
import hashlib
import hmac
from typing import Mapping

from app.models import EmailVerificationOutbox, EmailVerificationToken, User
from app.outbox_token import derive_verification_token


ALLOWED_TRANSITIONS = {
    "queued": frozenset({"leased", "cancelled"}),
    "leased": frozenset({"leased", "queued", "delivered", "cancelled", "uncertain"}),
    "uncertain": frozenset(),
    "delivered": frozenset(),
    "cancelled": frozenset(),
}


def _utc(value: datetime | None) -> datetime | None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        return None
    return value.astimezone(UTC)


def _now(value: datetime) -> datetime:
    result = _utc(value)
    if result is None:
        raise ValueError("Outbox time must be timezone-aware")
    return result


def _attempt_limit(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("Outbox maximum attempts must be positive")
    return value


def _lease_matches(job: EmailVerificationOutbox, *, now: datetime,
                   owner: bytes, generation: int) -> bool:
    expires = _utc(job.lease_expires_at)
    return (job.state == "leased" and isinstance(owner, bytes) and len(owner) == 16
            and isinstance(job.lease_owner, bytes) and len(job.lease_owner) == 16
            and hmac.compare_digest(owner, job.lease_owner)
            and isinstance(generation, int) and not isinstance(generation, bool)
            and generation > 0 and job.fencing_generation == generation
            and expires is not None and now < expires)


def can_claim(job: EmailVerificationOutbox, *, now: datetime, max_attempts: int) -> bool:
    """A claim, including a crashed lease's replacement, consumes one attempt."""
    checked_at = _now(now)
    limit = _attempt_limit(max_attempts)
    created, expires, due = _utc(job.created_at), _utc(job.expires_at), _utc(job.next_attempt_at)
    if (created is None or expires is None or due is None
            or not created <= due <= checked_at < expires
            or not isinstance(job.attempts, int) or not 0 <= job.attempts < limit
            or not isinstance(job.fencing_generation, int) or job.fencing_generation < 0):
        return False
    if job.state == "queued":
        return job.lease_owner is None and job.lease_expires_at is None
    lease_expires = _utc(job.lease_expires_at)
    return (job.state == "leased" and isinstance(job.lease_owner, bytes)
            and len(job.lease_owner) == 16 and job.fencing_generation > 0
            and lease_expires is not None and lease_expires <= checked_at)


def can_finish(job: EmailVerificationOutbox, target: str, *, now: datetime,
               owner: bytes, generation: int, max_attempts: int,
               retry_at: datetime | None = None) -> bool:
    """Validate a live worker lease before completing or scheduling a retry."""
    checked_at = _now(now)
    limit = _attempt_limit(max_attempts)
    expires = _utc(job.expires_at)
    if (target not in ALLOWED_TRANSITIONS["leased"] - {"leased"}
            or expires is None or not checked_at < expires
            or not isinstance(job.attempts, int) or not 1 <= job.attempts <= limit
            or not _lease_matches(job, now=checked_at, owner=owner, generation=generation)):
        return False
    if target == "queued":
        scheduled = _utc(retry_at)
        return job.attempts < limit and scheduled is not None and checked_at <= scheduled < expires
    return retry_at is None


def can_cancel_obsolete(job: EmailVerificationOutbox) -> bool:
    """Only for a future user-first, token-second, outbox-last locked transaction."""
    return job.state in {"queued", "leased"}


def eligible_for_delivery(*, user: User | None, token: EmailVerificationToken | None,
                          job: EmailVerificationOutbox, keys: Mapping[str, bytes],
                          now: datetime, max_attempts: int,
                          owner: bytes, generation: int) -> bool:
    """Recheck a live lease and derive only in memory; never return the bearer."""
    checked_at = _now(now)
    limit = _attempt_limit(max_attempts)
    if user is None or token is None or user.is_active or user.email_verified_at is not None:
        return False
    if (not isinstance(user.id, int) or not isinstance(token.id, int)
            or token.user_id != user.id or job.token_id != token.id
            or token.consumed_at is not None
            or not _lease_matches(job, now=checked_at, owner=owner, generation=generation)
            or not isinstance(job.attempts, int) or not 1 <= job.attempts <= limit):
        return False
    token_created, token_expires = _utc(token.created_at), _utc(token.expires_at)
    job_created, job_expires = _utc(job.created_at), _utc(job.expires_at)
    job_due = _utc(job.next_attempt_at)
    if (token_created is None or token_expires is None or job_created is None
            or job_expires is None or job_due is None or token_created > job_created
            or not job_created <= job_due <= checked_at
            or job_expires > token_expires or not job_created <= checked_at < job_expires):
        return False
    secret = keys.get(job.key_version) if isinstance(job.key_version, str) else None
    if secret is None:
        return False
    try:
        bearer = derive_verification_token(secret, token_id=token.id,
                                           nonce=job.nonce, key_version=job.key_version)
    except ValueError:
        return False
    return (isinstance(token.token_hash, bytes) and len(token.token_hash) == 32
            and hmac.compare_digest(hashlib.sha256(bearer.encode("ascii")).digest(),
                                    token.token_hash))
