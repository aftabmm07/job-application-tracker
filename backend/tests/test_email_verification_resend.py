"""Offline resend transaction tests with independent file-backed SQLite sessions."""
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.auth_config import AuthSettings
from app.email_verification import (
    VerificationStorageError, activate_and_commit_verified_user,
    generate_verification_token, resend_verification_for_user,
)
from app.models import Base, EmailVerificationToken, User


class FakeSender:
    def __init__(self, failure=None):
        self.sent = []
        self.failure = failure

    def send_verification(self, recipient, link, expires_at):
        if self.failure is not None:
            raise self.failure
        self.sent.append((recipient, link, expires_at))


@pytest.fixture
def database(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'resend.sqlite'}",
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    now = datetime.now(UTC)
    raw, digest = generate_verification_token()
    with Session(engine) as db:
        user = User(email="resend@example.com", password_hash="$argon2id$test-placeholder")
        db.add(user)
        db.flush()
        user_id = user.id
        db.add(EmailVerificationToken(user_id=user_id, token_hash=digest,
                                      created_at=now - timedelta(minutes=20),
                                      expires_at=now + timedelta(minutes=40)))
        db.commit()
    settings = AuthSettings.from_environment({
        "VERIFICATION_FRONTEND_ORIGIN": "http://localhost:5173",
    })
    try:
        yield engine, user_id, raw, now, settings
    finally:
        engine.dispose()


def rows(engine):
    with Session(engine) as db:
        return db.scalars(select(EmailVerificationToken).order_by(EmailVerificationToken.id)).all()


def test_cooldown_rechecks_latest_token_and_preserves_old_link(database, monkeypatch):
    engine, user_id, raw, now, settings = database
    monkeypatch.setattr("app.email_verification.utc_now", lambda: now)
    with Session(engine) as db:
        db.scalar(select(EmailVerificationToken)).created_at = now - timedelta(minutes=9)
        db.commit()
    sender = FakeSender()
    with Session(engine) as db:
        assert not resend_verification_for_user(db=db, user_id=user_id, sender=sender, settings=settings)
        assert not db.in_transaction()
    assert sender.sent == []
    assert len(rows(engine)) == 1 and rows(engine)[0].consumed_at is None
    with Session(engine) as db:
        assert activate_and_commit_verified_user(db=db, raw_token=raw)


def test_cooldown_uses_latest_of_multiple_tokens(database, monkeypatch):
    engine, user_id, _, now, settings = database
    monkeypatch.setattr("app.email_verification.utc_now", lambda: now)
    _, digest = generate_verification_token()
    with Session(engine) as db:
        db.add(EmailVerificationToken(user_id=user_id, token_hash=digest,
                                      created_at=now - timedelta(minutes=2),
                                      expires_at=now + timedelta(minutes=58)))
        db.commit()
    sender = FakeSender()
    with Session(engine) as db:
        assert not resend_verification_for_user(db=db, user_id=user_id, sender=sender, settings=settings)
    assert len(rows(engine)) == 2
    assert all(row.consumed_at is None for row in rows(engine))
    assert sender.sent == []


@pytest.mark.parametrize("verified, active", [(True, False), (True, True)])
def test_ineligible_account_is_not_rotated(database, monkeypatch, verified, active):
    engine, user_id, _, now, settings = database
    monkeypatch.setattr("app.email_verification.utc_now", lambda: now)
    with Session(engine) as db:
        user = db.get(User, user_id)
        user.email_verified_at = now if verified else None
        user.is_active = active
        db.commit()
    sender = FakeSender()
    with Session(engine) as db:
        assert not resend_verification_for_user(db=db, user_id=user_id, sender=sender, settings=settings)
    assert sender.sent == [] and len(rows(engine)) == 1
    assert rows(engine)[0].consumed_at is None


def test_success_rotates_hash_and_old_token_cannot_activate(database, monkeypatch, caplog):
    import hashlib
    engine, user_id, old_raw, now, settings = database
    monkeypatch.setattr("app.email_verification.utc_now", lambda: now)
    sender = FakeSender()
    with Session(engine) as db:
        assert resend_verification_for_user(db=db, user_id=user_id, sender=sender, settings=settings)
        assert not db.in_transaction()
    old, new = rows(engine)
    assert old.consumed_at == now
    assert new.consumed_at is None
    assert new.created_at == now and new.expires_at == now + timedelta(hours=1)
    assert len(sender.sent) == 1
    recipient, link, expiry = sender.sent[0]
    assert recipient == "resend@example.com" and expiry == new.expires_at
    assert link.startswith("http://localhost:5173/verify-email#token=")
    new_raw = link.split("#token=", 1)[1]
    assert new.token_hash == hashlib.sha256(new_raw.encode("ascii")).digest()
    assert new_raw not in repr(new) + caplog.text
    with Session(engine) as db:
        assert not activate_and_commit_verified_user(db=db, raw_token=old_raw)
    with Session(engine) as db:
        assert activate_and_commit_verified_user(db=db, raw_token=new_raw)
    with Session(engine) as db:
        assert not activate_and_commit_verified_user(db=db, raw_token=new_raw)


def test_rotation_consumes_every_previous_unused_token(database, monkeypatch):
    engine, user_id, _, now, settings = database
    monkeypatch.setattr("app.email_verification.utc_now", lambda: now)
    _, digest = generate_verification_token()
    with Session(engine) as db:
        db.add(EmailVerificationToken(user_id=user_id, token_hash=digest,
                                      created_at=now - timedelta(minutes=15),
                                      expires_at=now + timedelta(minutes=45)))
        db.commit()
    sender = FakeSender()
    with Session(engine) as db:
        assert resend_verification_for_user(db=db, user_id=user_id, sender=sender, settings=settings)
    old_first, old_second, new = rows(engine)
    assert old_first.consumed_at == old_second.consumed_at == now
    assert new.consumed_at is None
    assert len(sender.sent) == 1


def test_delivery_failure_rolls_back_rotation_and_hides_error(database, monkeypatch, caplog):
    engine, user_id, raw, now, settings = database
    monkeypatch.setattr("app.email_verification.utc_now", lambda: now)
    secret = "private-delivery-error"
    sender = FakeSender(RuntimeError(secret))
    with Session(engine) as db:
        assert not resend_verification_for_user(db=db, user_id=user_id, sender=sender, settings=settings)
        assert not db.in_transaction()
    assert len(rows(engine)) == 1 and rows(engine)[0].consumed_at is None
    assert secret not in caplog.text
    with Session(engine) as db:
        assert activate_and_commit_verified_user(db=db, raw_token=raw)


def test_sender_exception_containing_link_is_not_logged(database, monkeypatch, caplog):
    engine, user_id, _, now, settings = database
    monkeypatch.setattr("app.email_verification.utc_now", lambda: now)

    class FailingSender:
        def send_verification(self, recipient, link, expires_at):
            raise RuntimeError(link)

    with Session(engine) as db:
        assert not resend_verification_for_user(db=db, user_id=user_id,
                                                 sender=FailingSender(), settings=settings)
    assert len(rows(engine)) == 1 and rows(engine)[0].consumed_at is None
    assert "#token=" not in caplog.text


@pytest.mark.parametrize("commit_completed", [False, True])
def test_commit_error_is_sanitized_without_claiming_outcome(database, monkeypatch, commit_completed, caplog):
    engine, user_id, _, now, settings = database
    monkeypatch.setattr("app.email_verification.utc_now", lambda: now)
    sender = FakeSender()
    with Session(engine) as db:
        real_commit = db.commit

        def failed_commit():
            if commit_completed:
                real_commit()
            raise SQLAlchemyError("private-commit-error")

        monkeypatch.setattr(db, "commit", failed_commit)
        with pytest.raises(VerificationStorageError, match="storage is unavailable"):
            resend_verification_for_user(db=db, user_id=user_id, sender=sender, settings=settings)
    assert len(sender.sent) == 1
    assert len(rows(engine)) == (2 if commit_completed else 1)
    assert "private-commit-error" not in caplog.text


def test_competing_resends_issue_once_after_cooldown_recheck(database, monkeypatch):
    engine, user_id, _, now, settings = database
    monkeypatch.setattr("app.email_verification.utc_now", lambda: now)
    first_sending = Event()
    release_first = Event()
    second_writing = Event()

    class BlockingSender(FakeSender):
        def send_verification(self, recipient, link, expires_at):
            first_sending.set()
            assert release_first.wait(3)
            super().send_verification(recipient, link, expires_at)

    first_sender, second_sender = BlockingSender(), FakeSender()

    def attempt(sender):
        with Session(engine) as db:
            return resend_verification_for_user(db=db, user_id=user_id, sender=sender, settings=settings)

    def observe(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("UPDATE email_verification_tokens"):
            second_writing.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(attempt, first_sender)
        assert first_sending.wait(2)
        event.listen(engine, "before_cursor_execute", observe)
        try:
            second = pool.submit(attempt, second_sender)
            assert second_writing.wait(2)
            release_first.set()
            assert first.result(timeout=4) is True
            assert second.result(timeout=4) is False
        finally:
            release_first.set()
            event.remove(engine, "before_cursor_execute", observe)
    assert len(first_sender.sent) == 1 and second_sender.sent == []
    assert len(rows(engine)) == 2


def test_activation_reads_owner_then_user_before_locking_token(database):
    engine, _, raw, _, _ = database
    statements = []

    def observe(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(" ".join(statement.lower().split()))

    event.listen(engine, "before_cursor_execute", observe)
    try:
        with Session(engine) as db:
            assert activate_and_commit_verified_user(db=db, raw_token=raw)
    finally:
        event.remove(engine, "before_cursor_execute", observe)
    owner = next(i for i, sql in enumerate(statements)
                 if "from email_verification_tokens" in sql and "email_verification_tokens.user_id" in sql
                 and "email_verification_tokens.id" not in sql)
    user = next(i for i, sql in enumerate(statements) if "from users" in sql)
    token = next(i for i, sql in enumerate(statements)
                 if "from email_verification_tokens" in sql and "email_verification_tokens.id" in sql)
    assert owner < user < token
