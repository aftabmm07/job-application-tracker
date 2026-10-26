"""Dormant outbox model and pure derivation tests on disposable SQLite only."""
import base64
from datetime import UTC, datetime, timedelta
import hashlib
import hmac

import pytest
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Base, EmailVerificationOutbox, EmailVerificationToken, User
from app.outbox_token import derive_verification_token


KEY = bytes(range(32))
NONCE = bytes(range(32, 64))


@pytest.fixture
def engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'outbox.sqlite'}")
    Base.metadata.create_all(engine)  # Disposable test database only.
    try:
        yield engine
    finally:
        engine.dispose()


def stage(db, *, nonce=NONCE):
    now = datetime.now(UTC)
    user = User(email="outbox@example.com", password_hash="$argon2id$synthetic-test-hash")
    db.add(user)
    db.flush()
    token = EmailVerificationToken(user_id=user.id, token_hash=b"x" * 32,
                                   created_at=now, expires_at=now + timedelta(hours=1))
    db.add(token)
    db.flush()
    bearer = derive_verification_token(KEY, token_id=token.id, nonce=nonce, key_version="v1")
    token.token_hash = hashlib.sha256(bearer.encode("ascii")).digest()
    job = EmailVerificationOutbox(token_id=token.id, nonce=nonce, key_version="v1",
                                  created_at=now, next_attempt_at=now,
                                  expires_at=token.expires_at)
    db.add(job)
    db.flush()
    return token, job, bearer


def test_derivation_is_deterministic_versioned_and_matches_lookup_hash():
    bearer = derive_verification_token(KEY, token_id=7, nonce=NONCE, key_version="v1")
    assert bearer == derive_verification_token(KEY, token_id=7, nonce=NONCE, key_version="v1")
    assert len(bearer) == 43
    assert base64.urlsafe_b64decode(bearer + "=")
    assert bearer != derive_verification_token(KEY, token_id=7, nonce=NONCE, key_version="v2")
    assert bearer != derive_verification_token(bytes(reversed(KEY)), token_id=7, nonce=NONCE, key_version="v1")
    assert bearer != derive_verification_token(KEY, token_id=8, nonce=NONCE, key_version="v1")
    assert bearer != derive_verification_token(KEY, token_id=7, nonce=bytes(reversed(NONCE)), key_version="v1")
    # A different purpose cannot produce the same bearer even with the same key.
    unrelated = base64.urlsafe_b64encode(hmac.new(KEY, b"csrf:v1:" + NONCE, hashlib.sha256).digest()).rstrip(b"=").decode()
    assert bearer != unrelated
    digest = hashlib.sha256(bearer.encode("ascii")).digest()
    assert len(digest) == 32


@pytest.mark.parametrize("override", [
    {"secret": b"short"}, {"secret": b"x" * 32}, {"secret": "not-bytes"},
    {"token_id": 0}, {"token_id": True}, {"token_id": 2**31},
    {"nonce": b"short"}, {"nonce": bytearray(NONCE)},
    {"key_version": ""}, {"key_version": "V1"}, {"key_version": "v1/private"},
])
def test_invalid_derivation_inputs_are_rejected_without_secret_echo(override, caplog):
    values = dict(secret=KEY, token_id=7, nonce=NONCE, key_version="v1") | override
    with pytest.raises(ValueError) as captured:
        derive_verification_token(**values)
    assert repr(KEY) not in str(captured.value) + caplog.text


def test_token_and_outbox_commit_together_without_plaintext_fields(engine):
    with Session(engine) as db:
        token, job, bearer = stage(db)
        assert bearer not in repr(job) + repr(token)
        db.commit()
        token_id = token.id
    with Session(engine) as db:
        token = db.get(EmailVerificationToken, token_id)
        job = db.scalar(select(EmailVerificationOutbox).where(EmailVerificationOutbox.token_id == token_id))
        recovered = derive_verification_token(KEY, token_id=token_id, nonce=job.nonce, key_version=job.key_version)
        assert hashlib.sha256(recovered.encode("ascii")).digest() == token.token_hash
        assert job.state == "queued" and job.attempts == job.fencing_generation == 0
        assert bearer not in repr(job) + repr(token)
    columns = {column["name"] for column in inspect(engine).get_columns("email_verification_outbox")}
    assert columns == {
        "id", "token_id", "nonce", "key_version", "state", "created_at", "next_attempt_at",
        "expires_at", "attempts", "lease_expires_at", "lease_owner", "fencing_generation",
        "failure_category",
    }
    assert not {"token", "link", "recipient", "message", "secret", "ciphertext"} & columns


def test_token_and_outbox_rollback_together(engine):
    with Session(engine) as db:
        stage(db)
        db.rollback()
    with Session(engine) as db:
        assert db.scalars(select(User)).all() == []
        assert db.scalars(select(EmailVerificationToken)).all() == []
        assert db.scalars(select(EmailVerificationOutbox)).all() == []


def test_random_nonce_default_is_distinct_for_separate_jobs(engine):
    with Session(engine) as db:
        _, first, _ = stage(db)
        second_token = EmailVerificationToken(
            user_id=db.scalar(select(User.id)), token_hash=b"z" * 32,
            created_at=first.created_at, expires_at=first.expires_at,
        )
        db.add(second_token)
        db.flush()
        second = EmailVerificationOutbox(
            token_id=second_token.id, key_version="v1",
            created_at=first.created_at, next_attempt_at=first.created_at,
            expires_at=first.expires_at,
        )
        db.add(second)
        db.flush()
        assert len(second.nonce) == 32
        assert second.nonce != first.nonce
