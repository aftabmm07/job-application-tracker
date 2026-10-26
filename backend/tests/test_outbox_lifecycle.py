"""Offline policy checks only: no worker, SMTP, routes, or PostgreSQL."""

from datetime import UTC, datetime, timedelta, timezone
import hashlib
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models import Base, EmailVerificationOutbox, EmailVerificationToken, User
from app.outbox_lifecycle import (
    ALLOWED_TRANSITIONS, can_cancel_obsolete, can_claim, can_finish,
    eligible_for_delivery,
)
from app.outbox_token import derive_verification_token


NOW = datetime(2030, 1, 1, 12, tzinfo=UTC)
KEY = bytes(range(32))  # Synthetic test key only.
OWNER = b"o" * 16
NONCE = b"n" * 32


@pytest.fixture
def records():
    user = SimpleNamespace(id=1, is_active=False, email_verified_at=None)
    token = SimpleNamespace(id=2, user_id=1, consumed_at=None,
                            created_at=NOW - timedelta(minutes=5),
                            expires_at=NOW + timedelta(minutes=55))
    bearer = derive_verification_token(KEY, token_id=token.id, nonce=NONCE, key_version="v1")
    token.token_hash = hashlib.sha256(bearer.encode("ascii")).digest()
    job = SimpleNamespace(token_id=token.id, nonce=NONCE, key_version="v1",
                          state="leased", created_at=token.created_at,
                          next_attempt_at=token.created_at, expires_at=token.expires_at,
                          attempts=1, lease_owner=OWNER,
                          lease_expires_at=NOW + timedelta(minutes=1),
                          fencing_generation=1)
    return user, token, job


def eligible(records, **overrides):
    user, token, job = records
    args = dict(user=user, token=token, job=job, keys={"v1": KEY},
                now=NOW, max_attempts=3, owner=OWNER, generation=1)
    args.update(overrides)
    return eligible_for_delivery(**args)


def test_valid_delivery_eligibility_and_utc_conversion(records):
    assert eligible(records)
    assert eligible(records, now=NOW.astimezone(timezone(timedelta(hours=2))))


def test_job_may_expire_before_its_token(records):
    records[2].expires_at = NOW + timedelta(minutes=30)
    assert eligible(records)


@pytest.mark.parametrize("target,value", [
    ("user.is_active", True), ("user.email_verified_at", NOW),
    ("token.user_id", 99), ("token.consumed_at", NOW),
    ("token.token_hash", b"x" * 32),
    ("token.expires_at", NOW + timedelta(minutes=54)),
    ("job.token_id", 99), ("job.nonce", b"x" * 32),
    ("job.key_version", "v2"), ("job.key_version", "INVALID"),
    ("job.expires_at", NOW), ("job.state", "cancelled"),
    ("job.next_attempt_at", NOW + timedelta(seconds=1)),
    ("job.lease_expires_at", NOW), ("job.attempts", 4),
])
def test_ineligible_or_obsolete_records(records, target, value):
    object_name, attribute = target.split(".")
    setattr({"user": records[0], "token": records[1], "job": records[2]}[object_name],
            attribute, value)
    assert not eligible(records)


def test_missing_and_invalid_keys_are_ineligible(records):
    assert not eligible(records, keys={})
    assert not eligible(records, keys={"v1": b"weak"})
    assert not eligible(records, user=None)
    assert not eligible(records, token=None)


@pytest.mark.parametrize("state,targets", [
    ("queued", {"leased", "cancelled"}),
    ("leased", {"leased", "queued", "delivered", "cancelled", "uncertain"}),
    ("uncertain", set()), ("delivered", set()), ("cancelled", set()),
])
def test_explicit_state_transition_contract(state, targets):
    assert ALLOWED_TRANSITIONS[state] == targets
    assert "leased" not in ALLOWED_TRANSITIONS["uncertain"]


def test_claim_due_job_and_expired_lease_only(records):
    job = records[2]
    job.state, job.attempts = "queued", 0
    job.lease_owner = job.lease_expires_at = None
    assert can_claim(job, now=NOW, max_attempts=3)
    job.next_attempt_at = NOW + timedelta(seconds=1)
    assert not can_claim(job, now=NOW, max_attempts=3)
    job.next_attempt_at = NOW
    job.state, job.lease_owner, job.lease_expires_at = "leased", OWNER, NOW
    job.attempts, job.fencing_generation = 1, 1
    assert can_claim(job, now=NOW, max_attempts=3)
    job.lease_expires_at = NOW + timedelta(seconds=1)
    assert not can_claim(job, now=NOW, max_attempts=3)
    job.lease_expires_at = NOW
    job.attempts = 3
    assert not can_claim(job, now=NOW, max_attempts=3)
    job.attempts = 1
    for state in ("uncertain", "delivered", "cancelled"):
        job.state = state
        assert not can_claim(job, now=NOW, max_attempts=3)


@pytest.mark.parametrize("target", ["delivered", "uncertain", "cancelled"])
def test_current_lease_can_finish_but_stale_worker_cannot(records, target):
    job = records[2]
    args = dict(now=NOW, owner=OWNER, generation=1, max_attempts=3)
    assert can_finish(job, target, **args)
    assert not can_finish(job, target, **(args | {"owner": b"x" * 16}))
    assert not can_finish(job, target, **(args | {"generation": 0}))
    assert not can_finish(job, target, **(args | {"generation": 2}))
    assert not can_finish(job, target, **(args | {"now": job.lease_expires_at}))
    assert not can_finish(job, target, **(args | {"now": job.expires_at}))


def test_retry_requires_live_lease_remaining_attempts_and_valid_schedule(records):
    job = records[2]
    args = dict(now=NOW, owner=OWNER, generation=1, max_attempts=3)
    assert can_finish(job, "queued", retry_at=NOW + timedelta(minutes=1), **args)
    assert not can_finish(job, "queued", **args)
    assert not can_finish(job, "queued", retry_at=job.expires_at, **args)
    assert not can_finish(job, "queued", retry_at=NOW - timedelta(seconds=1), **args)
    job.attempts = 3
    assert not can_finish(job, "queued", retry_at=NOW, **args)
    assert can_finish(job, "uncertain", **args)


def test_only_nonterminal_jobs_can_be_cancelled(records):
    job = records[2]
    for state in ("queued", "leased"):
        job.state = state
        assert can_cancel_obsolete(job)
    for state in ("uncertain", "delivered", "cancelled"):
        job.state = state
        assert not can_cancel_obsolete(job)


def test_expiry_and_policy_inputs_fail_closed(records):
    job = records[2]
    job.state, job.lease_owner, job.lease_expires_at = "queued", None, None
    assert not can_claim(job, now=job.expires_at, max_attempts=3)
    for invalid in (0, -1, True):
        with pytest.raises(ValueError):
            can_claim(job, now=NOW, max_attempts=invalid)
    with pytest.raises(ValueError):
        eligible(records, now=NOW.replace(tzinfo=None))


def test_disposable_sqlite_round_trip_rechecks_hash_and_associations(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'lifecycle.sqlite'}")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            user = User(email="lifecycle@example.com", password_hash="$argon2id$synthetic-test-hash")
            db.add(user)
            db.flush()
            token = EmailVerificationToken(user_id=user.id, token_hash=b"x" * 32,
                                           created_at=NOW - timedelta(minutes=5),
                                           expires_at=NOW + timedelta(minutes=55))
            db.add(token)
            db.flush()
            bearer = derive_verification_token(KEY, token_id=token.id, nonce=NONCE, key_version="v1")
            token.token_hash = hashlib.sha256(bearer.encode("ascii")).digest()
            job = EmailVerificationOutbox(token_id=token.id, nonce=NONCE, key_version="v1",
                                          state="leased", created_at=token.created_at,
                                          next_attempt_at=token.created_at, expires_at=token.expires_at,
                                          attempts=1, lease_owner=OWNER,
                                          lease_expires_at=NOW + timedelta(minutes=1),
                                          fencing_generation=1)
            db.add(job)
            db.commit()
        with Session(engine) as db:
            user = db.scalar(select(User))
            token = db.scalar(select(EmailVerificationToken))
            job = db.scalar(select(EmailVerificationOutbox))
            assert eligible_for_delivery(user=user, token=token, job=job, keys={"v1": KEY},
                                         now=NOW, max_attempts=3, owner=OWNER, generation=1)
            token.consumed_at = NOW
            db.flush()
            assert not eligible_for_delivery(user=user, token=token, job=job, keys={"v1": KEY},
                                             now=NOW, max_attempts=3, owner=OWNER, generation=1)
    finally:
        engine.dispose()
