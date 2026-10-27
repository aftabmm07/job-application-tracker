from datetime import UTC, datetime, timedelta, timezone
import hashlib

import pytest
from sqlalchemy import create_engine, delete, insert, select, text
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm import Session

from app.models import AuthSession, Base, JobApplication, User
from app.passwords import hash_password


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        assert session.scalar(text("PRAGMA foreign_keys")) == 1
        yield session
    engine.dispose()


@pytest.fixture(scope="module")
def password_hash():
    return hash_password("a model test password")


def make_session(user_id=None, token=b"test-session"):
    now = datetime.now(UTC)
    return AuthSession(token_hash=hashlib.sha256(token).digest(), user_id=user_id,
                       created_at=now, last_activity_at=now,
                       expires_at=now + timedelta(hours=8), idle_expires_at=now + timedelta(minutes=30))


def test_normalization_uniqueness_and_inactive_defaults(db, password_hash):
    user = User(email="  Person@Example.com ", password_hash=password_hash)
    db.add(user)
    db.commit()
    db.refresh(user)
    assert user.email == "person@example.com"
    assert not user.is_active
    assert user.email_verified_at is None
    assert user.created_at.tzinfo == UTC
    db.add(User(email="PERSON@example.com", password_hash=password_hash))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


@pytest.mark.parametrize("values", [
    {"email": "UPPER@example.com"}, {"email": " person@example.com"},
    {"email": ""}, {"email": "x" * 321}, {"password_hash": "plaintext"},
    {"is_active": True},
])
def test_database_user_constraints_even_without_orm(db, password_hash, values):
    with pytest.raises(IntegrityError):
        db.execute(insert(User).values({"email": "person@example.com", "password_hash": password_hash} | values))
    db.rollback()


def test_verified_activation_and_relationships(db, password_hash):
    user = User(email="person@example.com", password_hash=password_hash,
                is_active=True, email_verified_at=datetime.now(UTC))
    application = JobApplication(company_name="Example", job_title="Engineer", user=user)
    session = make_session()
    session.user = user
    db.add_all([user, application, session])
    db.commit()
    assert application in user.applications
    assert session in user.sessions
    assert application.user_id == user.id


def test_unique_digest_anonymous_session_and_utc(db):
    session = make_session()
    session.created_at = session.created_at.astimezone(timezone(timedelta(hours=2)))
    db.add(session)
    db.commit()
    db.refresh(session)
    assert session.user_id is None
    assert len(session.token_hash) == 32
    for name in ("created_at", "last_activity_at", "expires_at", "idle_expires_at"):
        assert getattr(session, name).tzinfo == UTC
    db.add(make_session())
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


@pytest.mark.parametrize("kind", ["application", "session"])
def test_foreign_keys_reject_missing_user(db, kind):
    item = (JobApplication(company_name="Example", job_title="Engineer", user_id=999)
            if kind == "application" else make_session(user_id=999))
    db.add(item)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


@pytest.mark.parametrize("attribute", ["token_hash", "expires_at", "idle_expires_at", "last_activity_at"])
def test_session_constraints(db, attribute):
    session = make_session()
    value = {"token_hash": b"raw-token", "expires_at": session.created_at,
             "idle_expires_at": session.expires_at + timedelta(seconds=1),
             "last_activity_at": session.created_at - timedelta(seconds=1)}[attribute]
    setattr(session, attribute, value)
    db.add(session)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_naive_auth_timestamps_are_rejected(db):
    session = make_session()
    session.expires_at = datetime(2030, 1, 1)
    db.add(session)
    with pytest.raises(StatementError, match="timezone-aware"):
        db.commit()
    db.rollback()


def test_user_delete_revokes_sessions(db, password_hash):
    user = User(email="person@example.com", password_hash=password_hash)
    db.add(user)
    db.flush()
    db.add(make_session(user_id=user.id))
    db.commit()
    db.execute(delete(User).where(User.id == user.id))
    db.commit()
    assert db.scalar(select(AuthSession.id)) is None


def test_user_delete_cannot_delete_or_orphan_applications(db, password_hash):
    user = User(email="person@example.com", password_hash=password_hash)
    application = JobApplication(company_name="Example", job_title="Engineer", user=user)
    db.add(application)
    db.commit()
    db.delete(user)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    assert db.get(JobApplication, application.id).user_id == user.id
