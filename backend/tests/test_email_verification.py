from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.models import Base, EmailVerificationToken, User


def test_email_verification_token_can_be_stored():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    try:
        Base.metadata.create_all(engine)

        with Session(engine) as db:
            user = User(
                email="verify@example.com",
                password_hash="$argon2id$test-placeholder",
                is_active=False,
            )
            db.add(user)
            db.flush()

            now = datetime.now(UTC)

            token = EmailVerificationToken(
                user_id=user.id,
                token_hash=b"x" * 32,
                created_at=now,
                expires_at=now + timedelta(hours=1),
            )

            db.add(token)
            db.commit()

            stored = db.scalar(select(EmailVerificationToken))

            assert stored is not None
            assert stored.user_id == user.id
            assert stored.token_hash == b"x" * 32
            assert stored.consumed_at is None

    finally:
        engine.dispose()

def test_generated_verification_token_is_secure():
    from app.email_verification import generate_verification_token
    from app.session_service import token_digest

    raw_token, hashed_token = generate_verification_token()

    assert isinstance(raw_token, str)
    assert len(raw_token) == 43
    assert isinstance(hashed_token, bytes)
    assert len(hashed_token) == 32
    assert hashed_token == token_digest(raw_token)

    second_token, second_hash = generate_verification_token()

    assert second_token != raw_token
    assert second_hash != hashed_token

def test_expired_verification_token_is_rejected():
    from app.email_verification import is_verification_token_valid

    now = datetime.now(UTC)

    expires_at = now - timedelta(seconds=1)

    assert is_verification_token_valid(
        expires_at=expires_at,
        consumed_at=None,
        now=now,
    ) is False

def test_valid_verification_token_is_accepted():
    from app.email_verification import is_verification_token_valid

    now = datetime.now(UTC)

    assert is_verification_token_valid(
        expires_at=now + timedelta(minutes=30),
        consumed_at=None,
        now=now,
    ) is True


def test_consumed_verification_token_is_rejected():
    from app.email_verification import is_verification_token_valid

    now = datetime.now(UTC)

    assert is_verification_token_valid(
        expires_at=now + timedelta(minutes=30),
        consumed_at=now - timedelta(minutes=5),
        now=now,
    ) is False


def test_verification_token_expires_at_exact_boundary():
    from app.email_verification import is_verification_token_valid

    now = datetime.now(UTC)

    assert is_verification_token_valid(
        expires_at=now,
        consumed_at=None,
        now=now,
    ) is False

def test_valid_token_activates_user_and_is_consumed():
    from app.email_verification import activate_verified_user

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    try:
        Base.metadata.create_all(engine)

        with Session(engine) as db:
            now = datetime.now(UTC)

            user = User(
                email="activation@example.com",
                password_hash="$argon2id$test-placeholder",
                is_active=False,
            )
            db.add(user)
            db.flush()

            raw_token = "a" * 43

            from app.session_service import token_digest

            verification = EmailVerificationToken(
                user_id=user.id,
                token_hash=token_digest(raw_token),
                created_at=now,
                expires_at=now + timedelta(hours=1),
            )

            db.add(verification)
            db.commit()

            result = activate_verified_user(
                db=db,
                raw_token=raw_token,
                now=now,
            )

            assert result is True

            db.refresh(user)
            db.refresh(verification)

            assert user.is_active is True
            assert user.email_verified_at is not None
            assert verification.consumed_at is not None

    finally:
        engine.dispose()