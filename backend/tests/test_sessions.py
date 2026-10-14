from datetime import UTC, datetime, timedelta, timezone
from http.cookies import SimpleCookie
import hashlib
import traceback
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.responses import Response

from app.auth_config import AuthSettings
from app.models import AuthSession, Base, User
from app.passwords import hash_password
from app.session_http import clear_session_cookie, set_session_cookie
from app.session_service import (
    InvalidSessionError, SessionService, SessionStorageError, commit_session_changes, token_digest,
)

NOW = datetime(2026, 10, 14, 12, tzinfo=UTC)


@pytest.fixture
def settings():
    return AuthSettings.from_environment({})


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db
    engine.dispose()


@pytest.fixture(scope="module")
def password_hash():
    return hash_password("session service test password")


@pytest.fixture
def user(db, password_hash):
    user = User(email="session@example.com", password_hash=password_hash,
                is_active=True, email_verified_at=NOW)
    db.add(user)
    db.commit()
    return user


def service(db, settings, now=NOW):
    return SessionService(db, settings, clock=lambda: now)


def test_creation_hashes_random_token_and_keeps_it_out_of_record(db, settings):
    with patch("app.session_service.secrets.token_urlsafe", wraps=__import__('secrets').token_urlsafe) as random:
        issued = service(db, settings).create()
        random.assert_called_once_with(32)
    commit_session_changes(db)
    record = db.get(AuthSession, issued.session_id)
    assert record.user_id is None
    assert record.token_hash == hashlib.sha256(issued.raw_token.encode('ascii')).digest()
    assert len(record.token_hash) == 32
    assert not hasattr(record, 'raw_token')
    assert issued.raw_token not in repr(issued)
    assert issued.expires_at == NOW + timedelta(minutes=15)
    assert record.idle_expires_at == record.expires_at
    other = service(db, settings).create()
    commit_session_changes(db)
    assert other.raw_token != issued.raw_token
    assert token_digest(other.raw_token) != record.token_hash


def test_authenticated_session_and_read_only_lookup(db, settings, user):
    issued = service(db, settings).create(user.id)
    commit_session_changes(db)
    record = service(db, settings, NOW + timedelta(seconds=30)).lookup(issued.raw_token)
    assert record.user_id == user.id
    assert record.created_at == NOW
    assert record.last_activity_at == NOW
    assert record.expires_at == NOW + timedelta(hours=8)
    assert record.idle_expires_at == NOW + timedelta(minutes=30)


@pytest.mark.parametrize('active,verified', [(False, False), (False, True)])
def test_ineligible_user_cannot_receive_authenticated_session(db, settings, password_hash, active, verified):
    user = User(email='inactive@example.com', password_hash=password_hash, is_active=active,
                email_verified_at=NOW if verified else None)
    db.add(user)
    db.commit()
    with pytest.raises(InvalidSessionError):
        service(db, settings).create(user.id)
    assert db.scalar(select(func.count()).select_from(AuthSession)) == 0


def test_missing_user_is_rejected(db, settings):
    with pytest.raises(InvalidSessionError):
        service(db, settings).create(999)


def test_disabled_account_invalidates_existing_session(db, settings, user):
    issued = service(db, settings).create(user.id)
    commit_session_changes(db)
    user.is_active = False
    db.commit()
    assert service(db, settings).lookup(issued.raw_token) is None
    assert service(db, settings).renew(issued.raw_token) is None


@pytest.mark.parametrize('seconds,valid', [(899, True), (900, False), (901, False), (-1, False)])
def test_anonymous_expiration_boundary(db, settings, seconds, valid):
    issued = service(db, settings).create()
    commit_session_changes(db)
    current = service(db, settings, NOW + timedelta(seconds=seconds))
    assert (current.lookup(issued.raw_token) is not None) is valid
    assert (current.renew(issued.raw_token) is not None) is valid


@pytest.mark.parametrize('seconds,valid', [(1799, True), (1800, False), (1801, False)])
def test_idle_expiry_boundary(db, settings, user, seconds, valid):
    issued = service(db, settings).create(user.id)
    commit_session_changes(db)
    current = service(db, settings, NOW + timedelta(seconds=seconds))
    assert (current.lookup(issued.raw_token) is not None) is valid
    assert (current.renew(issued.raw_token) is not None) is valid


def test_absolute_expiry_never_extended_and_idle_is_capped(db, user):
    settings = AuthSettings.from_environment({'SESSION_ABSOLUTE_TIMEOUT_SECONDS': '120',
                                               'SESSION_IDLE_TIMEOUT_SECONDS': '100'})
    issued = service(db, settings).create(user.id)
    commit_session_changes(db)
    renewed = service(db, settings, NOW + timedelta(seconds=90)).renew(issued.raw_token)
    commit_session_changes(db)
    assert renewed.last_activity_at == NOW + timedelta(seconds=90)
    assert renewed.idle_expires_at == issued.expires_at
    assert renewed.expires_at == NOW + timedelta(seconds=120)
    assert service(db, settings, NOW + timedelta(seconds=119)).lookup(issued.raw_token)
    assert service(db, settings, NOW + timedelta(seconds=120)).lookup(issued.raw_token) is None
    assert service(db, settings, NOW + timedelta(seconds=120)).renew(issued.raw_token) is None


def test_renewal_slides_idle_expiry_and_preserves_absolute(db, settings, user):
    issued = service(db, settings).create(user.id)
    commit_session_changes(db)
    record = service(db, settings, NOW + timedelta(minutes=20)).renew(issued.raw_token)
    commit_session_changes(db)
    assert record.last_activity_at == NOW + timedelta(minutes=20)
    assert record.idle_expires_at == NOW + timedelta(minutes=50)
    assert record.expires_at == issued.expires_at
    # An older in-flight clock must not move activity backwards.
    assert service(db, settings, NOW + timedelta(minutes=10)).renew(issued.raw_token) is None


def test_revocation_is_idempotent_and_renewal_cannot_resurrect(db, settings):
    issued = service(db, settings).create()
    commit_session_changes(db)
    current = service(db, settings)
    assert current.revoke(issued.raw_token)
    commit_session_changes(db)
    assert not current.revoke(issued.raw_token)
    assert current.lookup(issued.raw_token) is None
    assert current.renew(issued.raw_token) is None


@pytest.mark.parametrize('token', [None, '', 'x', 'x' * 10000, '🔒' * 43, '../' * 15])
def test_invalid_tokens_fail_closed_without_database_access(db, settings, token):
    current = service(db, settings)
    with patch.object(db, 'execute') as execute, patch.object(db, 'scalar') as scalar:
        assert current.lookup(token) is None
        assert current.renew(token) is None
        assert not current.revoke(token)
    execute.assert_not_called()
    scalar.assert_not_called()


def test_login_rotation_replaces_anonymous_session(db, settings, user):
    current = service(db, settings)
    old = current.create()
    commit_session_changes(db)
    new = current.rotate_for_login(old.raw_token, user.id)
    commit_session_changes(db)
    assert new.raw_token != old.raw_token
    assert current.lookup(old.raw_token) is None
    assert current.lookup(new.raw_token).user_id == user.id
    assert db.scalar(select(func.count()).select_from(AuthSession)) == 1
    with pytest.raises(InvalidSessionError):
        current.rotate_for_login(old.raw_token, user.id)
    with pytest.raises(InvalidSessionError):
        current.rotate_for_login(new.raw_token, user.id)


def test_rotation_rejects_expired_session(db, settings, user):
    old = service(db, settings).create()
    commit_session_changes(db)
    with pytest.raises(InvalidSessionError):
        service(db, settings, NOW + timedelta(minutes=15)).rotate_for_login(old.raw_token, user.id)


def test_rotation_failure_rolls_back_both_insert_and_deletion(db, settings, user):
    current = service(db, settings)
    old = current.create()
    commit_session_changes(db)
    user_id = user.id  # Resolve the expired ORM attribute before mocking DB access.
    with patch.object(db, 'execute', side_effect=SQLAlchemyError('private driver error')):
        with pytest.raises(SessionStorageError, match='Session storage is unavailable'):
            current.rotate_for_login(old.raw_token, user_id)
    assert current.lookup(old.raw_token) is not None
    assert db.scalar(select(func.count()).select_from(AuthSession)) == 1


def test_rotation_can_be_rolled_back_after_flush(db, settings, user):
    current = service(db, settings)
    old = current.create()
    commit_session_changes(db)
    new = current.rotate_for_login(old.raw_token, user.id)
    db.rollback()
    assert current.lookup(old.raw_token) is not None
    assert current.lookup(new.raw_token) is None


@pytest.mark.parametrize('operation,method', [('create', 'flush'), ('lookup', 'scalar'),
                                             ('renew', 'scalar'), ('revoke', 'execute')])
def test_database_failures_are_sanitized_and_rolled_back(db, settings, caplog, operation, method):
    current = service(db, settings)
    issued = current.create()
    commit_session_changes(db)
    with patch.object(db, method, side_effect=SQLAlchemyError(issued.raw_token)), patch.object(db, 'rollback', wraps=db.rollback) as rollback:
        with pytest.raises(SessionStorageError) as error:
            getattr(current, operation)(*(() if operation == 'create' else (issued.raw_token,)))
        rollback.assert_called_once()
    rendered = ''.join(traceback.format_exception(error.value))
    assert issued.raw_token not in rendered
    assert issued.raw_token not in caplog.text
    assert current.lookup(issued.raw_token) is not None


def test_commit_failure_does_not_leave_new_session_persisted(db, settings):
    issued = service(db, settings).create()
    with patch.object(db, 'commit', side_effect=SQLAlchemyError('private commit error')):
        with pytest.raises(SessionStorageError):
            commit_session_changes(db)
    assert service(db, settings).lookup(issued.raw_token) is None


def test_naive_clock_rejected_and_other_timezones_normalized(db, settings):
    with pytest.raises(ValueError, match='timezone-aware'):
        service(db, settings, NOW.replace(tzinfo=None)).create()
    issued = service(db, settings, NOW.astimezone(timezone(timedelta(hours=3)))).create()
    assert issued.expires_at == NOW + timedelta(minutes=15)


@pytest.mark.parametrize('production', [False, True])
def test_cookie_flags_expiry_and_deletion(db, production):
    values = {'APP_ENV': 'production', 'CSRF_HMAC_SECRET': 'test-only-production-secret-123456789',
              'AUTH_ALLOWED_ORIGINS': 'https://tracker.example.com'} if production else {}
    settings = AuthSettings.from_environment(values)
    issued = service(db, settings).create()
    commit_session_changes(db)
    response = Response()
    set_session_cookie(response, issued, settings, now=NOW + timedelta(seconds=5))
    cookie = SimpleCookie(response.headers['set-cookie'])[settings.session_cookie_name]
    assert cookie.value == issued.raw_token
    assert cookie['httponly']
    assert bool(cookie['secure']) is production
    assert cookie['samesite'] == 'lax'
    assert cookie['path'] == '/'
    assert cookie['domain'] == ''
    assert cookie['max-age'] == '895'
    assert cookie['expires']
    assert response.headers['cache-control'] == 'no-store'
    deleted = Response()
    clear_session_cookie(deleted, settings)
    cleared = SimpleCookie(deleted.headers['set-cookie'])[settings.session_cookie_name]
    assert cleared['max-age'] == '0'
    assert cleared['httponly']
    assert bool(cleared['secure']) is production
    assert cleared['path'] == '/' and cleared['domain'] == ''
    assert deleted.headers['cache-control'] == 'no-store'


def test_cannot_set_expired_cookie(db, settings):
    issued = service(db, settings).create()
    response = Response()
    with pytest.raises(ValueError, match='expired'):
        set_session_cookie(response, issued, settings, now=issued.expires_at)
    assert 'set-cookie' not in response.headers
