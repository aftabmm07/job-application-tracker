"""Activation tests use only disposable SQLite databases, never runtime engines."""
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import UTC, datetime, timedelta
from threading import Event

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app import email_verification as verification
from app.models import Base, EmailVerificationToken, User


@pytest.fixture
def database(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'verification.sqlite'}",
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    raw, digest = verification.generate_verification_token()
    now = datetime.now(UTC)
    with Session(engine) as db:
        user = User(email="activation@example.com", password_hash="$argon2id$test-placeholder")
        db.add(user)
        db.flush()
        db.add(EmailVerificationToken(user_id=user.id, token_hash=digest, created_at=now,
                                     expires_at=now + timedelta(hours=1)))
        db.commit()
    try:
        yield engine, raw, now
    finally:
        engine.dispose()


def state(engine):
    with Session(engine) as db:
        user = db.scalar(select(User))
        token = db.scalar(select(EmailVerificationToken))
        return user.is_active, user.email_verified_at, token.consumed_at


def test_activation_commits_both_changes_and_repeated_attempt_is_rejected(database):
    engine, raw, now = database
    with Session(engine) as db:
        assert verification.activate_verified_user(db=db, raw_token=raw, now=now)
        db.commit()
    assert state(engine) == (True, now, now)
    with Session(engine) as db:
        assert not verification.activate_verified_user(db=db, raw_token=raw, now=now)
        db.commit()
    assert state(engine) == (True, now, now)


@pytest.mark.parametrize("offset", [timedelta(hours=1), timedelta(hours=2)])
def test_expired_and_exact_expiration_boundary(database, offset):
    engine, raw, now = database
    with Session(engine) as db:
        assert not verification.activate_verified_user(db=db, raw_token=raw, now=now + offset)
        db.commit()
    assert state(engine) == (False, None, None)


@pytest.mark.parametrize("raw", [None, "", "invalid", "x" * 44, "é" * 43, "x" * 43])
def test_invalid_tokens_do_not_activate_or_leak(database, raw, caplog):
    engine, _, now = database
    with Session(engine) as db:
        assert not verification.activate_verified_user(db=db, raw_token=raw, now=now)
        db.commit()
    assert state(engine) == (False, None, None)
    assert not caplog.records


def test_consumed_token_cannot_activate_inactive_user(database):
    engine, raw, now = database
    with Session(engine) as db:
        db.scalar(select(EmailVerificationToken)).consumed_at = now
        db.commit()
        assert not verification.activate_verified_user(db=db, raw_token=raw, now=now)
        db.commit()
    assert state(engine) == (False, None, now)


def test_caller_rollback_restores_token_and_account(database):
    engine, raw, now = database
    with Session(engine) as db:
        assert verification.activate_verified_user(db=db, raw_token=raw, now=now)
        db.rollback()
    assert state(engine) == (False, None, None)
    with Session(engine) as db:
        assert verification.activate_verified_user(db=db, raw_token=raw, now=now)
        db.commit()


def test_database_failure_rolls_back_consumption_and_sanitizes_errors(database, caplog):
    engine, raw, now = database
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TRIGGER reject_activation BEFORE UPDATE ON users "
                                   "BEGIN SELECT RAISE(ABORT, 'synthetic-private-database-error'); END")
    with Session(engine) as db:
        with pytest.raises(verification.VerificationStorageError) as error:
            verification.activate_verified_user(db=db, raw_token=raw, now=now)
        assert str(error.value) == "Email verification storage is unavailable"
        assert error.value.__suppress_context__
        assert not db.in_transaction()
    assert state(engine) == (False, None, None)
    assert not caplog.records


def test_verification_does_not_reactivate_a_disabled_verified_account(database):
    engine, raw, now = database
    with Session(engine) as db:
        db.scalar(select(User)).email_verified_at = now
        db.commit()
        assert not verification.activate_verified_user(db=db, raw_token=raw, now=now)
        db.commit()
    assert state(engine) == (False, now, None)


def test_future_and_naive_times_are_rejected(database):
    engine, raw, now = database
    with Session(engine) as db:
        with pytest.raises(ValueError, match="timezone-aware"):
            verification.activate_verified_user(db=db, raw_token=raw, now=now.replace(tzinfo=None))
        assert not verification.activate_verified_user(db=db, raw_token=raw, now=now - timedelta(seconds=1))
        db.rollback()
    assert state(engine) == (False, None, None)


@pytest.mark.parametrize("first_commits", [True, False])
def test_competing_sqlite_sessions_only_one_consumes_token(database, first_commits):
    engine, raw, now = database
    attempting_write = Event()

    def observe(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("UPDATE email_verification_tokens"):
            attempting_write.set()

    def competing_attempt():
        with Session(engine) as db:
            result = verification.activate_verified_user(db=db, raw_token=raw, now=now)
            db.commit()
            return result

    with Session(engine) as first, ThreadPoolExecutor(max_workers=1) as pool:
        assert verification.activate_verified_user(db=first, raw_token=raw, now=now)
        event.listen(engine, "before_cursor_execute", observe)
        try:
            second = pool.submit(competing_attempt)
            assert attempting_write.wait(1), "Competing writer did not reach its lock acquisition"
            if first_commits:
                first.commit()
            else:
                first.rollback()
            assert second.result(timeout=4) is (not first_commits)
        finally:
            first.rollback()
            event.remove(engine, "before_cursor_execute", observe)
    assert state(engine) == (True, now, now)


def test_wall_clock_is_sampled_after_sqlite_writer_reservation(database, monkeypatch):
    engine, raw, now = database
    reserved = False

    def observe(connection, cursor, statement, parameters, context, executemany):
        nonlocal reserved
        if statement.startswith("UPDATE email_verification_tokens"):
            reserved = True

    def clock():
        assert reserved
        return now + timedelta(hours=1)

    monkeypatch.setattr(verification, "utc_now", clock)
    event.listen(engine, "after_cursor_execute", observe)
    try:
        with Session(engine) as db:
            assert not verification.activate_verified_user(db=db, raw_token=raw)
            db.commit()
    finally:
        event.remove(engine, "after_cursor_execute", observe)
    assert state(engine) == (False, None, None)


def test_endpoint_boundary_returns_success_only_after_real_commit(database, monkeypatch):
    engine, raw, now = database
    monkeypatch.setattr(verification, "utc_now", lambda: now)
    with Session(engine) as db:
        assert verification.activate_and_commit_verified_user(db=db, raw_token=raw)
        assert not db.in_transaction()
    assert state(engine) == (True, now, now)


@pytest.mark.parametrize("commit_completed", [False, True])
def test_commit_exception_never_returns_success_even_when_outcome_is_uncertain(
        database, monkeypatch, caplog, commit_completed):
    engine, raw, now = database
    monkeypatch.setattr(verification, "utc_now", lambda: now)
    responses = []
    with Session(engine) as db:
        original_commit, original_rollback = db.commit, db.rollback
        rollback_calls = []

        def failing_commit():
            if commit_completed:
                original_commit()
            raise SQLAlchemyError("synthetic-private-commit-detail")

        def rollback():
            rollback_calls.append(True)
            original_rollback()

        monkeypatch.setattr(db, "commit", failing_commit)
        monkeypatch.setattr(db, "rollback", rollback)
        with pytest.raises(verification.VerificationStorageError) as error:
            # Models the future endpoint returning only after the boundary succeeds.
            responses.append(verification.activate_and_commit_verified_user(db=db, raw_token=raw))
        assert not responses
        assert rollback_calls == [True]
        assert not db.in_transaction()
        assert str(error.value) == "Email verification storage is unavailable"
        assert error.value.__suppress_context__
    assert state(engine) == ((True, now, now) if commit_completed else (False, None, None))
    assert not caplog.records


def test_commit_and_rollback_failure_remain_sanitized(database, monkeypatch):
    engine, raw, now = database
    with Session(engine) as db:
        assert verification.activate_verified_user(db=db, raw_token=raw, now=now)
        original_rollback = db.rollback

        def failure():
            raise SQLAlchemyError("synthetic-private-database-detail")

        monkeypatch.setattr(db, "commit", failure)
        monkeypatch.setattr(db, "rollback", failure)
        with pytest.raises(verification.VerificationStorageError, match="^Email verification storage is unavailable$"):
            verification.commit_verification_changes(db)
        # An unusable connection cannot promise successful rollback. Explicitly
        # restore the test session to release its actual SQLite writer lock.
        original_rollback()
    assert state(engine) == (False, None, None)


def test_invalid_endpoint_attempt_releases_transaction(database):
    engine, raw, now = database
    with Session(engine) as db:
        assert not verification.activate_and_commit_verified_user(db=db, raw_token="x" * 43)
        assert not db.in_transaction()
    assert state(engine) == (False, None, None)


def test_sqlite_lock_timeout_is_sanitized_and_rolls_back(database):
    engine, raw, now = database

    def contender():
        with Session(engine) as db:
            with pytest.raises(verification.VerificationStorageError, match="storage is unavailable"):
                verification.activate_verified_user(db=db, raw_token=raw, now=now)
            assert not db.in_transaction()

    with Session(engine) as holder, ThreadPoolExecutor(max_workers=1) as pool:
        assert verification.activate_verified_user(db=holder, raw_token=raw, now=now)
        try:
            pool.submit(contender).result(timeout=5)
        finally:
            holder.rollback()
    assert state(engine) == (False, None, None)


def test_expiration_is_rechecked_after_real_sqlite_lock_wait(database, monkeypatch):
    engine, raw, now = database
    attempted = Event()
    clock_value = now
    monkeypatch.setattr(verification, "utc_now", lambda: clock_value)

    def observe(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("UPDATE email_verification_tokens"):
            attempted.set()

    def contender():
        with Session(engine) as db:
            return verification.activate_and_commit_verified_user(db=db, raw_token=raw)

    with Session(engine) as holder, ThreadPoolExecutor(max_workers=1) as pool:
        assert verification.activate_verified_user(db=holder, raw_token=raw, now=now)
        event.listen(engine, "before_cursor_execute", observe)
        try:
            future = pool.submit(contender)
            assert attempted.wait(1)
            # Keep the writer reservation held while the contender is pending.
            with pytest.raises(TimeoutError):
                future.result(timeout=0.05)
            clock_value = now + timedelta(hours=1)
            holder.rollback()
            assert future.result(timeout=4) is False
        finally:
            holder.rollback()
            event.remove(engine, "before_cursor_execute", observe)
    assert state(engine) == (False, None, None)
