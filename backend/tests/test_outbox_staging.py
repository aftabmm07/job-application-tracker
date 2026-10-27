"""Disposable SQLite tests for dormant token/outbox staging only."""

from datetime import UTC, datetime, timedelta
import hashlib
from unittest.mock import Mock, patch

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models import Base, EmailVerificationOutbox, EmailVerificationToken, User
from app.outbox_staging import OutboxStagingError, stage_verification_outbox
from app.outbox_token import derive_verification_token


NOW = datetime(2030, 1, 1, 12, tzinfo=UTC)
KEY = bytes(range(32))  # Synthetic, test-only dedicated key.


@pytest.fixture
def database(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'outbox-staging.sqlite'}")
    Base.metadata.create_all(engine)  # New disposable test file only.
    try:
        yield engine
    finally:
        engine.dispose()


def add_user(engine, *, active=False, verified_at=None):
    with Session(engine) as db:
        user = User(email="staging@example.com", password_hash="$argon2id$synthetic-test-hash",
                    is_active=active, email_verified_at=verified_at)
        db.add(user)
        db.commit()
        return user.id


def stage(db, user_id, **overrides):
    args = dict(db=db, user_id=user_id, key_version="v1",
                derivation_key=KEY, created_at=NOW)
    args.update(overrides)
    return stage_verification_outbox(**args)


def counts(engine):
    with Session(engine) as db:
        return (db.scalar(select(func.count()).select_from(EmailVerificationToken)),
                db.scalar(select(func.count()).select_from(EmailVerificationOutbox)))


def test_stages_exactly_one_pair_with_derived_hash_and_no_bearer_return(database, caplog):
    user_id = add_user(database)
    with Session(database) as db:
        staged = stage(db, user_id)
        assert staged.expires_at == NOW + timedelta(hours=1)
        assert set(vars(staged)) == {"token_id", "job_id", "expires_at"}
        db.commit()
    assert counts(database) == (1, 1)
    with Session(database) as db:
        token = db.get(EmailVerificationToken, staged.token_id)
        job = db.get(EmailVerificationOutbox, staged.job_id)
        bearer = derive_verification_token(KEY, token_id=token.id,
                                           nonce=job.nonce, key_version=job.key_version)
        assert len(bearer) == 43
        assert token.token_hash == hashlib.sha256(bearer.encode("ascii")).digest()
        assert job.token_id == token.id and job.state == "queued"
        assert job.next_attempt_at == job.created_at == NOW
        assert job.expires_at == token.expires_at
        assert job.attempts == job.fencing_generation == 0
        assert job.lease_owner is None and job.lease_expires_at is None
        assert bearer not in repr(staged) + repr(job) + caplog.text


def test_fresh_nonce_for_each_pair_and_no_implicit_rotation(database):
    user_id = add_user(database)
    with Session(database) as db:
        first = stage(db, user_id)
        second = stage(db, user_id)
        db.commit()
    with Session(database) as db:
        jobs = db.scalars(select(EmailVerificationOutbox).order_by(EmailVerificationOutbox.id)).all()
        tokens = db.scalars(select(EmailVerificationToken).order_by(EmailVerificationToken.id)).all()
        assert len(jobs) == len(tokens) == 2
        assert jobs[0].token_id == first.token_id and jobs[1].token_id == second.token_id
        assert len(jobs[0].nonce) == len(jobs[1].nonce) == 32
        assert jobs[0].nonce != jobs[1].nonce
        assert all(token.consumed_at is None for token in tokens)


@pytest.mark.parametrize("override", [
    {"derivation_key": b"short"}, {"derivation_key": b"x" * 32},
    {"key_version": ""}, {"key_version": "V1"},
    {"key_version": "v1/private"},
])
def test_invalid_key_or_version_fails_before_database_use(override):
    db = Mock()
    with pytest.raises(ValueError) as error:
        stage(db, 1, **override)
    assert "short" not in str(error.value)
    db.get_bind.assert_not_called()
    db.execute.assert_not_called()


@pytest.mark.parametrize("active,verified_at", [
    (True, NOW), (False, NOW),
])
def test_ineligible_existing_user_cannot_stage(database, active, verified_at):
    user_id = add_user(database, active=active, verified_at=verified_at)
    with Session(database) as db:
        with pytest.raises(ValueError, match="not eligible"):
            stage(db, user_id)
        db.rollback()
    assert counts(database) == (0, 0)


def test_missing_user_cannot_stage(database):
    with Session(database) as db:
        with pytest.raises(ValueError, match="not eligible"):
            stage(db, 999)
        db.rollback()
    assert counts(database) == (0, 0)


@pytest.mark.parametrize("override", [
    {"created_at": NOW.replace(tzinfo=None)},
    {"due_at": NOW - timedelta(seconds=1)},
    {"due_at": NOW + timedelta(hours=1)},
    {"job_expires_at": NOW},
    {"job_expires_at": NOW + timedelta(hours=1, seconds=1)},
    {"due_at": NOW + timedelta(minutes=5), "job_expires_at": NOW + timedelta(minutes=5)},
])
def test_invalid_schedule_has_no_token_or_job(database, override):
    user_id = add_user(database)
    with Session(database) as db:
        with pytest.raises(ValueError):
            stage(db, user_id, **override)
        db.rollback()
    assert counts(database) == (0, 0)


def test_bounded_later_due_time_and_shorter_job_expiry(database):
    user_id = add_user(database)
    with Session(database) as db:
        stage(db, user_id, due_at=NOW + timedelta(minutes=2),
              job_expires_at=NOW + timedelta(minutes=30))
        db.commit()
    with Session(database) as db:
        token = db.scalar(select(EmailVerificationToken))
        job = db.scalar(select(EmailVerificationOutbox))
        assert token.expires_at == NOW + timedelta(hours=1)
        assert job.next_attempt_at == NOW + timedelta(minutes=2)
        assert job.expires_at == NOW + timedelta(minutes=30)


@pytest.mark.parametrize("failed_flush", [1, 2])
def test_flush_failure_is_sanitized_and_caught_failure_cannot_commit_pair(database, failed_flush):
    user_id = add_user(database)
    with Session(database) as db:
        real_flush = db.flush
        calls = 0

        def fail_on_selected_flush(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == failed_flush:
                raise SQLAlchemyError("private storage detail")
            return real_flush(*args, **kwargs)

        with patch.object(db, "flush", side_effect=fail_on_selected_flush):
            with pytest.raises(OutboxStagingError, match="Outbox storage unavailable") as error:
                stage(db, user_id)
        assert "private storage detail" not in str(error.value)
        db.commit()  # A caller that catches the failure cannot commit a placeholder.
    assert counts(database) == (0, 0)


def test_failure_after_first_flush_rolls_back_savepoint_before_caller_commit(database):
    user_id = add_user(database)
    real_derive = derive_verification_token
    derivations = 0

    def fail_after_insert(*args, **kwargs):
        nonlocal derivations
        derivations += 1
        if derivations == 2:
            raise ValueError("Invalid verification token ID")
        return real_derive(*args, **kwargs)

    with Session(database) as db:
        with patch("app.outbox_staging.derive_verification_token", side_effect=fail_after_insert):
            with pytest.raises(ValueError, match="Invalid verification token ID"):
                stage(db, user_id)
        assert counts(database) == (0, 0)
        db.commit()
    assert counts(database) == (0, 0)


def test_staging_failure_preserves_previously_flushed_outer_change(database):
    user_id = add_user(database)
    with Session(database) as db:
        user = db.get(User, user_id)
        user.email = "preserved@example.com"
        db.flush()
        real_flush = db.flush
        flushes = 0

        def fail_second_stage_flush(*args, **kwargs):
            nonlocal flushes
            flushes += 1
            if flushes == 2:
                raise SQLAlchemyError("private storage detail")
            return real_flush(*args, **kwargs)

        with patch.object(db, "flush", side_effect=fail_second_stage_flush):
            with pytest.raises(OutboxStagingError):
                stage(db, user_id)
        db.commit()
    with Session(database) as db:
        assert db.get(User, user_id).email == "preserved@example.com"
    assert counts(database) == (0, 0)


def test_pending_caller_changes_are_rejected_without_autoflush(database):
    user_id = add_user(database)
    with Session(database) as db:
        user = db.get(User, user_id)
        user.email = "pending@example.com"
        with patch.object(db, "flush", wraps=db.flush) as flush:
            with pytest.raises(ValueError, match="Flush caller changes"):
                stage(db, user_id)
            flush.assert_not_called()
        db.commit()
    with Session(database) as db:
        assert db.get(User, user_id).email == "pending@example.com"
    assert counts(database) == (0, 0)


def test_caller_nested_transaction_keeps_staged_pair_inside_outer_savepoint(database):
    user_id = add_user(database)
    with Session(database) as db:
        db.execute(select(User.id).where(User.id == user_id))
        # Start a real SQLite write transaction before nested SAVEPOINTs.
        user = db.get(User, user_id)
        user.email = "nested@example.com"
        db.flush()
        with db.begin_nested():
            stage(db, user_id)
        db.rollback()
    assert counts(database) == (0, 0)
    with Session(database) as db:
        assert db.get(User, user_id).email == "staging@example.com"


def test_failed_staging_inside_caller_savepoint_preserves_outer_work(database):
    user_id = add_user(database)
    with Session(database) as db:
        user = db.get(User, user_id)
        user.email = "outer@example.com"
        db.flush()  # Establish the outer SQLite write transaction.
        with db.begin_nested():
            real_flush = db.flush
            flushes = 0

            def fail_second_stage_flush(*args, **kwargs):
                nonlocal flushes
                flushes += 1
                if flushes == 2:
                    raise SQLAlchemyError("private storage detail")
                return real_flush(*args, **kwargs)

            with patch.object(db, "flush", side_effect=fail_second_stage_flush):
                with pytest.raises(OutboxStagingError):
                    stage(db, user_id)
        db.commit()
    with Session(database) as db:
        assert db.get(User, user_id).email == "outer@example.com"
    assert counts(database) == (0, 0)


def test_caller_rollback_removes_both_staged_rows(database):
    user_id = add_user(database)
    with Session(database) as db:
        stage(db, user_id)
        db.rollback()
    assert counts(database) == (0, 0)


@pytest.mark.parametrize("commit_completed", [False, True])
def test_failed_caller_commit_does_not_imply_a_known_outcome(database, commit_completed):
    user_id = add_user(database)
    with Session(database) as db:
        stage(db, user_id)
        real_commit = db.commit

        def failing_commit():
            if commit_completed:
                real_commit()
            raise SQLAlchemyError("private commit detail")

        with patch.object(db, "commit", side_effect=failing_commit):
            with pytest.raises(SQLAlchemyError):
                db.commit()
        db.rollback()  # Best effort; cannot undo a commit that already completed.
    assert counts(database) == ((1, 1) if commit_completed else (0, 0))
