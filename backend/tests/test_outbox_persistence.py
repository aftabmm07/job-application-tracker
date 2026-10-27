"""Offline file-backed SQLite tests for dormant conditional outbox writes."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models import Base, EmailVerificationOutbox, EmailVerificationToken, User
from app.outbox_persistence import (
    CancellationOutcome, OutboxClaimLost, OutboxStorageError,
    cancel_consumed_token_job, claim_job, mark_delivered, mark_uncertain,
    schedule_retry,
)


NOW = datetime(2030, 1, 1, 12, tzinfo=UTC)
LIMIT = 3
LEASE = timedelta(seconds=30)


@pytest.fixture
def database(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'outbox-writes.sqlite'}",
                           connect_args={"timeout": 2})
    Base.metadata.create_all(engine)  # New disposable file, never an existing DB.
    with Session(engine) as db:
        user = User(email="writes@example.com", password_hash="$argon2id$synthetic-test-hash")
        db.add(user)
        db.flush()
        token = EmailVerificationToken(user_id=user.id, token_hash=b"h" * 32,
                                       created_at=NOW - timedelta(minutes=1),
                                       expires_at=NOW + timedelta(minutes=10))
        db.add(token)
        db.flush()
        job = EmailVerificationOutbox(token_id=token.id, nonce=b"n" * 32, key_version="v1",
                                      created_at=token.created_at, next_attempt_at=token.created_at,
                                      expires_at=token.expires_at)
        db.add(job)
        db.commit()
        job_id = job.id
    try:
        yield engine, job_id
    finally:
        engine.dispose()


def current(engine, job_id):
    with Session(engine) as db:
        return db.get(EmailVerificationOutbox, job_id)


def claim(db, job_id, **overrides):
    args = dict(db=db, job_id=job_id, now=NOW,
                max_attempts=LIMIT, lease_duration=LEASE)
    args.update(overrides)
    return claim_job(**args)


def finish_args(db, job_id, lease, **overrides):
    args = dict(db=db, job_id=job_id, owner=lease.owner,
                generation=lease.generation, now=NOW,
                max_attempts=LIMIT)
    args.update(overrides)
    return args


def test_claim_and_delivery_require_caller_commit(database):
    engine, job_id = database
    with Session(engine) as db:
        lease = claim(db, job_id)
        assert len(lease.owner) == 16 and repr(lease.owner) not in repr(lease)
        assert lease.generation == 1 and lease.expires_at == NOW + LEASE
        db.commit()
    job = current(engine, job_id)
    assert (job.state, job.attempts, job.fencing_generation) == ("leased", 1, 1)
    with Session(engine) as db:
        mark_delivered(**finish_args(db, job_id, lease))
        db.commit()
    job = current(engine, job_id)
    assert job.state == "delivered" and job.lease_owner is None
    with Session(engine) as db:
        with pytest.raises(OutboxClaimLost):
            claim(db, job_id)
        db.rollback()


def test_claim_and_completion_rollback(database):
    engine, job_id = database
    with Session(engine) as db:
        claim(db, job_id)
        db.rollback()
    assert current(engine, job_id).state == "queued"
    with Session(engine) as db:
        lease = claim(db, job_id)
        db.commit()
    with Session(engine) as db:
        mark_uncertain(**finish_args(db, job_id, lease))
        db.rollback()
    assert current(engine, job_id).state == "leased"


def test_independent_sessions_compete_for_same_job(database):
    engine, job_id = database
    claimed = Event()
    contender_ready = Event()

    def first():
        with Session(engine) as db:
            lease = claim(db, job_id)
            claimed.set()
            assert contender_ready.wait(3)
            db.commit()
            return lease

    def second():
        assert claimed.wait(3)
        contender_ready.set()
        with Session(engine) as db:
            with pytest.raises(OutboxClaimLost):
                claim(db, job_id)
            db.rollback()

    with ThreadPoolExecutor(max_workers=2) as executor:
        a = executor.submit(first)
        b = executor.submit(second)
        lease = a.result(timeout=5)
        b.result(timeout=5)
    assert current(engine, job_id).lease_owner == lease.owner
    assert current(engine, job_id).attempts == 1


def test_reclaim_expired_lease_increments_generation_and_fences_prior_owner(database):
    engine, job_id = database
    with Session(engine) as db:
        old = claim(db, job_id)
        db.commit()
    with Session(engine) as db:
        new = claim(db, job_id, now=old.expires_at)
        db.commit()
    assert new.generation == old.generation + 1
    assert new.owner != old.owner
    assert current(engine, job_id).attempts == 2
    with Session(engine) as db:
        with pytest.raises(OutboxClaimLost):
            mark_delivered(**finish_args(db, job_id, old, now=old.expires_at))
        db.rollback()


@pytest.mark.parametrize("override", [
    {"owner": b"x" * 16}, {"generation": 2},
    {"now": NOW + LEASE}, {"now": NOW + timedelta(minutes=10)},
])
def test_completion_rejects_stale_lease(database, override):
    engine, job_id = database
    with Session(engine) as db:
        lease = claim(db, job_id)
        db.commit()
    with Session(engine) as db:
        with pytest.raises(OutboxClaimLost):
            mark_delivered(**finish_args(db, job_id, lease, **override))
        db.rollback()
    assert current(engine, job_id).state == "leased"


def test_definite_failure_retry_and_uncertain_outcome(database):
    engine, job_id = database
    with Session(engine) as db:
        lease = claim(db, job_id)
        db.commit()
    retry_at = NOW + timedelta(seconds=20)
    with Session(engine) as db:
        schedule_retry(**finish_args(db, job_id, lease), retry_at=retry_at)
        db.commit()
    job = current(engine, job_id)
    assert job.state == "queued" and job.next_attempt_at == retry_at
    assert job.lease_owner is None and job.failure_category == "transport"
    with Session(engine) as db:
        with pytest.raises(OutboxClaimLost):
            claim(db, job_id)
        db.rollback()
    with Session(engine) as db:
        replacement = claim(db, job_id, now=retry_at)
        db.commit()
    with Session(engine) as db:
        mark_uncertain(**finish_args(db, job_id, replacement, now=retry_at))
        db.commit()
    job = current(engine, job_id)
    assert job.state == "uncertain" and job.failure_category == "uncertain"
    with Session(engine) as db:
        with pytest.raises(OutboxClaimLost):
            claim(db, job_id, now=retry_at + timedelta(seconds=1))
        db.rollback()


def test_retry_exhaustion_and_job_expiry(database):
    engine, job_id = database
    with Session(engine) as db:
        job = db.get(EmailVerificationOutbox, job_id)
        job.attempts = LIMIT - 1
        db.commit()
    with Session(engine) as db:
        lease = claim(db, job_id)
        db.commit()
    assert current(engine, job_id).attempts == LIMIT
    with Session(engine) as db:
        with pytest.raises(OutboxClaimLost):
            schedule_retry(**finish_args(db, job_id, lease), retry_at=NOW + timedelta(seconds=1))
        db.rollback()
    with Session(engine) as db:
        with pytest.raises(OutboxClaimLost):
            claim(db, job_id, now=lease.expires_at)
        db.rollback()
    with Session(engine) as db:
        with pytest.raises(OutboxClaimLost):
            mark_uncertain(**finish_args(db, job_id, lease, now=NOW + timedelta(minutes=10)))
        db.rollback()


def test_claim_refuses_terminal_job(database):
    engine, job_id = database
    with Session(engine) as db:
        job = db.get(EmailVerificationOutbox, job_id)
        job.state = "cancelled"
        db.commit()
    with Session(engine) as db:
        with pytest.raises(OutboxClaimLost):
            claim(db, job_id)
        db.rollback()


def test_claim_refuses_expired_job(database):
    engine, job_id = database
    with Session(engine) as db:
        with pytest.raises(OutboxClaimLost):
            claim(db, job_id, now=NOW + timedelta(minutes=10))
        db.rollback()


def test_lease_expiry_is_capped_at_job_expiry(database):
    engine, job_id = database
    almost_expired = NOW + timedelta(minutes=9, seconds=50)
    with Session(engine) as db:
        lease = claim(db, job_id, now=almost_expired)
        db.commit()
    assert lease.expires_at == NOW + timedelta(minutes=10)


def test_invalid_policy_inputs_and_sanitized_storage_failure(database):
    engine, job_id = database
    with Session(engine) as db:
        with pytest.raises(ValueError):
            claim(db, job_id, max_attempts=0)
        with pytest.raises(ValueError):
            claim(db, job_id, lease_duration=timedelta(minutes=2))
        with pytest.raises(ValueError):
            claim(db, job_id, now=NOW.replace(tzinfo=None))
    fake = Mock()
    from sqlalchemy.exc import SQLAlchemyError
    fake.execute.side_effect = SQLAlchemyError("private SQL details")
    with pytest.raises(OutboxStorageError, match="Outbox storage unavailable") as error:
        claim(fake, job_id)
    assert "private SQL details" not in str(error.value)


def consume_and_cancel(db, *, user_id=1):
    token = db.scalar(select(EmailVerificationToken))
    token.consumed_at = NOW
    db.flush()  # Future caller must flush before checking the SQL predicate.
    return cancel_consumed_token_job(db=db, user_id=user_id, token_id=token.id)


def test_queued_cancellation_and_repeated_call_are_explicit(database):
    engine, job_id = database
    with Session(engine) as db:
        assert consume_and_cancel(db) is CancellationOutcome.CANCELLED
        assert consume_and_cancel(db) is CancellationOutcome.ALREADY_CANCELLED
        db.commit()
    job = current(engine, job_id)
    assert job.state == "cancelled"
    assert job.fencing_generation == 1
    assert job.lease_owner is None and job.lease_expires_at is None


def test_leased_cancellation_fences_stale_worker_writes(database):
    engine, job_id = database
    with Session(engine) as db:
        lease = claim(db, job_id)
        db.commit()
    with Session(engine) as db:
        assert consume_and_cancel(db) is CancellationOutcome.CANCELLED
        db.commit()
    job = current(engine, job_id)
    assert job.state == "cancelled" and job.fencing_generation == lease.generation + 1
    assert job.lease_owner is None and job.lease_expires_at is None
    for action in (
        lambda **args: mark_delivered(**args),
        lambda **args: schedule_retry(**args, retry_at=NOW + timedelta(seconds=1)),
        lambda **args: mark_uncertain(**args),
    ):
        with Session(engine) as db:
            with pytest.raises(OutboxClaimLost):
                action(**finish_args(db, job_id, lease))
            db.rollback()
    assert current(engine, job_id).state == "cancelled"


@pytest.mark.parametrize("terminal", ["delivered", "uncertain"])
def test_terminal_job_is_not_reported_as_newly_cancelled(database, terminal):
    engine, job_id = database
    with Session(engine) as db:
        lease = claim(db, job_id)
        db.commit()
    with Session(engine) as db:
        action = mark_delivered if terminal == "delivered" else mark_uncertain
        action(**finish_args(db, job_id, lease))
        db.commit()
    with Session(engine) as db:
        assert consume_and_cancel(db) is CancellationOutcome.TERMINAL
        db.commit()
    job = current(engine, job_id)
    assert job.state == terminal
    assert job.failure_category == ("uncertain" if terminal == "uncertain" else None)


def test_unconsumed_or_foreign_token_is_not_cancelled(database):
    engine, job_id = database
    with Session(engine) as db:
        token_id = db.scalar(select(EmailVerificationToken.id))
        assert cancel_consumed_token_job(db=db, user_id=1, token_id=token_id) is CancellationOutcome.NOT_ELIGIBLE
        assert cancel_consumed_token_job(db=db, user_id=2, token_id=token_id) is CancellationOutcome.NOT_ELIGIBLE
        db.rollback()
    assert current(engine, job_id).state == "queued"


def test_consumption_and_cancellation_roll_back_together(database):
    engine, job_id = database
    with Session(engine) as db:
        assert consume_and_cancel(db) is CancellationOutcome.CANCELLED
        db.rollback()
    with Session(engine) as db:
        assert db.scalar(select(EmailVerificationToken)).consumed_at is None
        assert db.get(EmailVerificationOutbox, job_id).state == "queued"


def test_storage_failure_can_roll_back_both_changes(database):
    engine, job_id = database
    with Session(engine) as db:
        token = db.scalar(select(EmailVerificationToken))
        token.consumed_at = NOW
        db.flush()
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(db, "execute", Mock(side_effect=SQLAlchemyError("private SQL details")))
            with pytest.raises(OutboxStorageError, match="Outbox storage unavailable") as error:
                cancel_consumed_token_job(db=db, user_id=1, token_id=token.id)
        assert "private SQL details" not in str(error.value)
        db.rollback()
    with Session(engine) as db:
        assert db.scalar(select(EmailVerificationToken)).consumed_at is None
        assert db.get(EmailVerificationOutbox, job_id).state == "queued"


def test_completion_wins_before_cancellation_in_independent_sessions(database):
    engine, job_id = database
    with Session(engine) as db:
        lease = claim(db, job_id)
        db.commit()
    completion_staged = Event()
    cancellation_started = Event()

    def complete():
        with Session(engine) as db:
            mark_delivered(**finish_args(db, job_id, lease))
            completion_staged.set()
            assert cancellation_started.wait(3)
            db.commit()

    def cancel():
        assert completion_staged.wait(3)
        with Session(engine) as db:
            cancellation_started.set()
            outcome = consume_and_cancel(db)
            db.commit()
            return outcome

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(complete)
        second = executor.submit(cancel)
        first.result(timeout=5)
        assert second.result(timeout=5) is CancellationOutcome.TERMINAL
    assert current(engine, job_id).state == "delivered"
