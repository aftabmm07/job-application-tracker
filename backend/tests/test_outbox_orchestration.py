"""Offline transaction examples for the disabled outbox; never invoke HTTP or SMTP."""

from datetime import UTC, datetime, timedelta
import hashlib
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models import Base, EmailVerificationOutbox as Job, EmailVerificationToken as Token, User
from app.outbox_orchestration import stage_activation, stage_registration, stage_resend
from app.outbox_persistence import claim_job, mark_uncertain
from app.outbox_staging import OutboxStagingError, stage_verification_outbox
from app.outbox_token import derive_verification_token


NOW = datetime(2030, 1, 1, 12, tzinfo=UTC)
KEY = bytes(range(32))  # Synthetic dedicated key, never a deployed secret.
HASH = "$argon2id$synthetic-test-hash"


@pytest.fixture
def database(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'outbox-orchestration.sqlite'}")
    Base.metadata.create_all(engine)  # Disposable test file only.
    try:
        yield engine
    finally:
        engine.dispose()


def register(db, **overrides):
    args = dict(db=db, email="orchestration@example.com", password_hash=HASH,
                key_version="v1", derivation_key=KEY, clock=lambda: NOW)
    args.update(overrides)
    return stage_registration(**args)


def resend(db, user_id, at, **overrides):
    args = dict(db=db, user_id=user_id, key_version="v1",
                derivation_key=KEY, clock=lambda: at)
    args.update(overrides)
    return stage_resend(**args)


def bearer(db, token_id):
    """Transient test-only reconstruction; never returned from production helpers."""
    token = db.get(Token, token_id)
    job = db.scalar(select(Job).where(Job.token_id == token_id))
    raw = derive_verification_token(KEY, token_id=token.id, nonce=job.nonce,
                                    key_version=job.key_version)
    assert token.token_hash == hashlib.sha256(raw.encode("ascii")).digest()
    return raw


def rows(engine):
    with Session(engine) as db:
        return tuple(db.scalar(select(func.count()).select_from(model))
                     for model in (User, Token, Job))


def registered(engine):
    with Session(engine) as db:
        user_id, staged = register(db)
        db.commit()
    return user_id, staged


def historical(db, user_id, specs):
    """Insert disposable test history with sparse explicit IDs and mixed states."""
    for token_id, consumed, state, expired in specs:
        created = NOW - timedelta(hours=2) if expired else NOW - timedelta(minutes=10)
        expiry = created + timedelta(hours=1)
        token = Token(id=token_id, user_id=user_id,
                      token_hash=hashlib.sha256(f"historical-{token_id}".encode()).digest(),
                      created_at=created, expires_at=expiry,
                      consumed_at=NOW - timedelta(minutes=5) if consumed else None)
        db.add(token)
        db.flush()
        if state is not None:
            db.add(Job(token_id=token_id, nonce=bytes([token_id]) * 32,
                       key_version="v1", state=state, created_at=created,
                       next_attempt_at=created, expires_at=expiry,
                       lease_owner=b"o" * 16 if state == "leased" else None,
                       lease_expires_at=NOW + timedelta(minutes=1)
                       if state == "leased" else None,
                       fencing_generation=1 if state == "leased" else 0,
                       attempts=1 if state == "leased" else 0,
                       failure_category="uncertain" if state == "uncertain" else None))
    db.flush()


def test_registration_requires_one_caller_commit_and_returns_no_bearer(database, caplog):
    with Session(database) as db:
        user_id, staged = register(db)
        assert rows(database) == (0, 0, 0)
        assert set(vars(staged)) == {"token_id", "job_id", "expires_at"}
        db.commit()
    assert rows(database) == (1, 1, 1)
    with Session(database) as db:
        user = db.get(User, user_id)
        assert user.is_active is False and user.email_verified_at is None
        raw = bearer(db, staged.token_id)
        assert raw not in repr(staged) + repr(user) + caplog.text


def test_registration_rollback_and_staging_failure_leave_no_rows(database):
    with Session(database) as db:
        register(db)
        db.rollback()
    assert rows(database) == (0, 0, 0)
    with Session(database) as db:
        with patch("app.outbox_orchestration.stage_verification_outbox",
                   side_effect=OutboxStagingError("Outbox storage unavailable")):
            with pytest.raises(OutboxStagingError):
                register(db)
        db.rollback()  # The user flush preceded the private staging savepoint.
    assert rows(database) == (0, 0, 0)


def test_registration_rejects_duplicate_email_without_new_pair(database):
    registered(database)
    with Session(database) as db:
        with pytest.raises(SQLAlchemyError):
            register(db)
        db.rollback()
    assert rows(database) == (1, 1, 1)


def test_resend_cooldown_and_repeated_attempts(database):
    user_id, first = registered(database)
    with Session(database) as db:
        with patch("app.outbox_orchestration._lock_relevant_tokens") as lock_history:
            assert resend(db, user_id, NOW + timedelta(minutes=9)) is None
            lock_history.assert_not_called()
        db.rollback()
    assert rows(database) == (1, 1, 1)
    with Session(database) as db:
        replacement = resend(db, user_id, NOW + timedelta(minutes=10))
        assert replacement is not None
        db.commit()
    with Session(database) as db:
        assert db.get(Token, first.token_id).consumed_at is not None
        assert db.get(Job, first.job_id).state == "cancelled"
        assert db.get(Token, replacement.token_id).consumed_at is None
        assert resend(db, user_id, NOW + timedelta(minutes=11)) is None
        db.rollback()
    assert rows(database) == (1, 2, 2)


def test_resend_cancels_leased_job_and_keeps_uncertain_audit_state(database):
    user_id, first = registered(database)
    with Session(database) as db:
        lease = claim_job(db=db, job_id=first.job_id,
                          now=NOW + timedelta(minutes=1), max_attempts=3,
                          lease_duration=timedelta(seconds=30))
        db.commit()
    with Session(database) as db:
        replacement = resend(db, user_id, NOW + timedelta(minutes=11))
        db.commit()
    with Session(database) as db:
        cancelled = db.get(Job, first.job_id)
        assert cancelled.state == "cancelled"
        assert cancelled.fencing_generation > lease.generation
        second_lease = claim_job(db=db, job_id=replacement.job_id,
                                 now=NOW + timedelta(minutes=12), max_attempts=3,
                                 lease_duration=timedelta(seconds=30))
        db.commit()
    with Session(database) as db:
        mark_uncertain(db=db, job_id=replacement.job_id, owner=second_lease.owner,
                       generation=second_lease.generation,
                       now=NOW + timedelta(minutes=12), max_attempts=3)
        db.commit()
    with Session(database) as db:
        resend(db, user_id, NOW + timedelta(minutes=22))
        db.commit()
    with Session(database) as db:
        uncertain = db.get(Job, replacement.job_id)
        assert uncertain.state == "uncertain" and uncertain.failure_category == "uncertain"
        assert db.get(Token, replacement.token_id).consumed_at is not None


def test_resend_locks_tokens_in_stable_id_order(database):
    user_id, _ = registered(database)
    with Session(database) as db:
        second = resend(db, user_id, NOW + timedelta(minutes=11))
        db.commit()
    visited = []
    from app.outbox_persistence import cancel_consumed_token_job

    def record(*, db, user_id, token_id):
        visited.append(token_id)
        return cancel_consumed_token_job(db=db, user_id=user_id, token_id=token_id)

    with Session(database) as db:
        with patch("app.outbox_orchestration.cancel_consumed_token_job", side_effect=record):
            resend(db, user_id, NOW + timedelta(minutes=22))
        db.commit()
    assert visited == [second.token_id]  # First token is consumed and cancelled.


def test_batched_resend_covers_sparse_ids_and_mixed_historical_jobs(database):
    user_id, first = registered(database)
    with Session(database) as db:
        historical(db, user_id, [
            (10, True, "queued", False),
            (20, True, "leased", False),
            (30, True, "uncertain", False),
            (40, False, "delivered", False),
            (50, False, None, False),
            (60, True, "queued", True),  # Expired, consumed, but job actionable.
            (70, True, "cancelled", False),
        ])
        db.commit()
    visited = []
    from app.outbox_persistence import cancel_consumed_token_job

    def record(*, db, user_id, token_id):
        visited.append(token_id)
        return cancel_consumed_token_job(db=db, user_id=user_id, token_id=token_id)

    with Session(database) as db:
        with patch("app.outbox_orchestration.cancel_consumed_token_job", side_effect=record):
            replacement = resend(db, user_id, NOW + timedelta(minutes=11), batch_size=2)
        db.commit()
    assert visited == [first.token_id, 10, 20, 40, 50, 60]
    assert rows(database) == (1, 9, 8)  # One historic token has no job.
    with Session(database) as db:
        for token_id in (first.token_id, 10, 20, 40, 50, 60):
            assert db.get(Token, token_id).consumed_at is not None
        for token_id in (first.token_id, 10, 20, 60):
            assert db.scalar(select(Job).where(Job.token_id == token_id)).state == "cancelled"
        leased = db.scalar(select(Job).where(Job.token_id == 20))
        assert leased.fencing_generation == 2 and leased.lease_owner is None
        assert db.scalar(select(Job).where(Job.token_id == 30)).state == "uncertain"
        assert db.scalar(select(Job).where(Job.token_id == 30)).failure_category == "uncertain"
        assert db.scalar(select(Job).where(Job.token_id == 40)).state == "delivered"
        assert db.get(Token, replacement.token_id).consumed_at is None


def test_exact_batch_boundary_and_later_batch_failure_rollback(database):
    user_id, first = registered(database)
    with Session(database) as db:
        historical(db, user_id, [(10, False, "queued", False),
                                 (20, False, "queued", False),
                                 (40, False, "queued", False)])
        db.commit()
    from app.outbox_persistence import cancel_consumed_token_job
    visited = []

    def fail_in_later_batch(*, db, user_id, token_id):
        visited.append(token_id)
        if len(visited) == 4:
            raise SQLAlchemyError("private late-batch detail")
        return cancel_consumed_token_job(db=db, user_id=user_id, token_id=token_id)

    with Session(database) as db:
        with patch("app.outbox_orchestration.cancel_consumed_token_job",
                   side_effect=fail_in_later_batch):
            with pytest.raises(SQLAlchemyError):
                resend(db, user_id, NOW + timedelta(minutes=11), batch_size=2)
        db.rollback()
    assert visited == [first.token_id, 10, 20, 40]
    with Session(database) as db:
        for token_id in visited:
            assert db.get(Token, token_id).consumed_at is None
            assert db.scalar(select(Job).where(Job.token_id == token_id)).state == "queued"
        assert resend(db, user_id, NOW + timedelta(minutes=11), batch_size=2)
        db.commit()
    assert rows(database) == (1, 5, 5)


def test_batched_activation_finds_selected_token_after_sparse_history(database):
    user_id, first = registered(database)
    with Session(database) as db:
        historical(db, user_id, [(10, True, "queued", True),
                                 (20, False, "queued", False),
                                 (40, True, "uncertain", False),
                                 (70, False, "leased", False)])
        selected = stage_verification_outbox(
            db=db, user_id=user_id, key_version="v1", derivation_key=KEY,
            created_at=NOW + timedelta(minutes=1),
        )
        db.commit()
    with Session(database) as db:
        raw = bearer(db, selected.token_id)
        assert stage_activation(db=db, raw_token=raw,
                                clock=lambda: NOW + timedelta(minutes=2), batch_size=2)
        db.commit()
    with Session(database) as db:
        assert db.get(User, user_id).is_active
        assert db.get(Token, first.token_id).consumed_at is not None
        assert db.get(Token, selected.token_id).consumed_at is not None
        for token_id in (10, 20, 70, selected.token_id):
            assert db.scalar(select(Job).where(Job.token_id == token_id)).state == "cancelled"
        assert db.scalar(select(Job).where(Job.token_id == 40)).state == "uncertain"


@pytest.mark.parametrize("invalid", [0, -1, 501, True, "2"])
def test_invalid_batch_size_is_rejected_before_database_access(invalid):
    from unittest.mock import Mock

    db = Mock()
    with pytest.raises(ValueError, match="batch size"):
        stage_resend(db=db, user_id=1, key_version="v1", derivation_key=KEY,
                     batch_size=invalid)
    db.get_bind.assert_not_called()


def test_resend_failure_rolls_back_consumption_and_cancellation(database):
    user_id, first = registered(database)
    with Session(database) as db:
        with patch("app.outbox_orchestration.stage_verification_outbox",
                   side_effect=OutboxStagingError("Outbox storage unavailable")):
            with pytest.raises(OutboxStagingError):
                resend(db, user_id, NOW + timedelta(minutes=11))
        db.rollback()
    with Session(database) as db:
        assert db.get(Token, first.token_id).consumed_at is None
        assert db.get(Job, first.job_id).state == "queued"
    assert rows(database) == (1, 1, 1)
    with Session(database) as db:
        with patch("app.outbox_orchestration.cancel_consumed_token_job",
                   side_effect=SQLAlchemyError("private detail")):
            with pytest.raises(SQLAlchemyError):
                resend(db, user_id, NOW + timedelta(minutes=11))
        db.rollback()
    assert rows(database) == (1, 1, 1)


def test_activation_consumes_all_tokens_and_jobs_once(database):
    user_id, first = registered(database)
    with Session(database) as db:
        second = resend(db, user_id, NOW + timedelta(minutes=11))
        db.commit()
    with Session(database) as db:
        raw = bearer(db, second.token_id)
        assert stage_activation(db=db, raw_token=raw,
                                clock=lambda: NOW + timedelta(minutes=12))
        db.commit()
    with Session(database) as db:
        assert db.get(User, user_id).is_active
        assert db.get(User, user_id).email_verified_at is not None
        assert db.get(Token, first.token_id).consumed_at is not None
        assert db.get(Token, second.token_id).consumed_at is not None
        assert db.get(Job, second.job_id).state == "cancelled"
        assert not stage_activation(db=db, raw_token=raw,
                                    clock=lambda: NOW + timedelta(minutes=13))
        db.rollback()


def test_replaced_bearer_cannot_activate_and_uncertain_job_stays_terminal(database):
    user_id, first = registered(database)
    with Session(database) as db:
        old_raw = bearer(db, first.token_id)
        replacement = resend(db, user_id, NOW + timedelta(minutes=11))
        db.commit()
    with Session(database) as db:
        assert not stage_activation(db=db, raw_token=old_raw,
                                    clock=lambda: NOW + timedelta(minutes=12))
        db.rollback()
        lease = claim_job(db=db, job_id=replacement.job_id,
                          now=NOW + timedelta(minutes=12), max_attempts=3,
                          lease_duration=timedelta(seconds=30))
        db.commit()
    with Session(database) as db:
        mark_uncertain(db=db, job_id=replacement.job_id, owner=lease.owner,
                       generation=lease.generation,
                       now=NOW + timedelta(minutes=12), max_attempts=3)
        db.commit()
    with Session(database) as db:
        current_raw = bearer(db, replacement.token_id)
        assert stage_activation(db=db, raw_token=current_raw,
                                clock=lambda: NOW + timedelta(minutes=13))
        db.commit()
    with Session(database) as db:
        assert db.get(User, user_id).is_active
        job = db.get(Job, replacement.job_id)
        assert job.state == "uncertain" and job.failure_category == "uncertain"


def test_activation_rejects_expired_stale_and_malformed_tokens(database):
    _, first = registered(database)
    with Session(database) as db:
        raw = bearer(db, first.token_id)
        for candidate, at in (("bad", NOW), (raw, NOW + timedelta(hours=1))):
            assert not stage_activation(db=db, raw_token=candidate, clock=lambda: at)
            db.rollback()
    with Session(database) as db:
        assert db.scalar(select(User)).is_active is False
        assert db.get(Token, first.token_id).consumed_at is None
        assert db.get(Job, first.job_id).state == "queued"


def test_activation_failure_rolls_back_user_token_and_job(database):
    user_id, first = registered(database)
    with Session(database) as db:
        raw = bearer(db, first.token_id)
        with patch("app.outbox_orchestration.cancel_consumed_token_job",
                   side_effect=SQLAlchemyError("private detail")):
            with pytest.raises(SQLAlchemyError):
                stage_activation(db=db, raw_token=raw, clock=lambda: NOW)
        db.rollback()
    with Session(database) as db:
        assert db.get(User, user_id).is_active is False
        assert db.get(Token, first.token_id).consumed_at is None
        assert db.get(Job, first.job_id).state == "queued"


@pytest.mark.parametrize("commit_completed", [False, True])
def test_final_commit_exception_has_uncertain_outcome(database, commit_completed):
    with Session(database) as db:
        register(db)
        real_commit = db.commit

        def failing_commit():
            if commit_completed:
                real_commit()
            raise SQLAlchemyError("private commit detail")

        with patch.object(db, "commit", side_effect=failing_commit):
            with pytest.raises(SQLAlchemyError):
                db.commit()
        db.rollback()  # Best effort; cannot undo an already completed commit.
    assert rows(database) == ((1, 1, 1) if commit_completed else (0, 0, 0))
