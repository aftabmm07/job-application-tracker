from datetime import UTC, datetime, timedelta, timezone
from http.cookies import SimpleCookie
import hashlib
import traceback
from unittest.mock import Mock, patch

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.sql.dml import Delete
from sqlalchemy.dialects import postgresql
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
    execute = db.execute

    def fail_on_delete(statement, *args, **kwargs):
        if isinstance(statement, Delete):
            raise SQLAlchemyError('private driver error')
        return execute(statement, *args, **kwargs)

    with patch.object(db, 'execute', side_effect=fail_on_delete):
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


@pytest.mark.parametrize('operation', ['renew', 'revoke', 'rotate_for_login'])
@pytest.mark.parametrize('boundary', ['absolute', 'idle'])
def test_expiration_is_checked_after_row_lock_returns(db, user, operation, boundary):
    settings = AuthSettings.from_environment({'SESSION_ABSOLUTE_TIMEOUT_SECONDS': '120',
                                               'SESSION_IDLE_TIMEOUT_SECONDS': '60'})
    issued = service(db, settings).create()
    record = db.get(AuthSession, issued.session_id)
    if boundary == 'absolute':
        record.idle_expires_at = record.expires_at
    db.commit()
    deadline = record.expires_at if boundary == 'absolute' else record.idle_expires_at
    current_time = [deadline - timedelta(seconds=1)]
    clock_reads = []

    def clock():
        clock_reads.append(current_time[0])
        return current_time[0]

    scalar = db.scalar

    def delayed_lock(statement, *args, **kwargs):
        result = scalar(statement, *args, **kwargs)
        if getattr(statement, '_for_update_arg', None) is not None:
            assert clock_reads == []  # No pre-lock time may authorize this write.
            current_time[0] = deadline
        return result

    current = SessionService(db, settings, clock=clock)
    user_id = user.id
    with patch.object(db, 'scalar', side_effect=delayed_lock):
        if operation == 'rotate_for_login':
            with pytest.raises(InvalidSessionError):
                current.rotate_for_login(issued.raw_token, user_id)
        else:
            result = getattr(current, operation)(issued.raw_token)
            assert result is (None if operation == 'renew' else False)
    db.rollback()
    assert clock_reads == [deadline]
    assert db.scalar(select(func.count()).select_from(AuthSession)) == 1
    assert db.get(AuthSession, issued.session_id).last_activity_at == NOW


def test_lookup_rechecks_expiry_after_a_stalled_read(db, settings):
    issued = service(db, settings).create()
    db.commit()
    times = iter([NOW, NOW + timedelta(minutes=15)])
    current = SessionService(db, settings, clock=lambda: next(times))
    assert current.lookup(issued.raw_token) is None


def test_renewal_uses_post_lock_activity_time(db, settings):
    issued = service(db, settings).create()
    db.commit()
    time = [NOW]
    scalar = db.scalar

    def delayed_lock(statement, *args, **kwargs):
        result = scalar(statement, *args, **kwargs)
        if getattr(statement, '_for_update_arg', None) is not None:
            time[0] = NOW + timedelta(minutes=5)
        return result

    with patch.object(db, 'scalar', side_effect=delayed_lock):
        renewed = SessionService(db, settings, clock=lambda: time[0]).renew(issued.raw_token)
    assert renewed.last_activity_at == NOW + timedelta(minutes=5)
    assert renewed.expires_at == issued.expires_at


@pytest.mark.parametrize('operation', ['renew', 'revoke'])
def test_old_token_after_rotation_cannot_modify_new_session(db, settings, user, operation):
    current = service(db, settings)
    old = current.create()
    db.commit()
    new = current.rotate_for_login(old.raw_token, user.id)
    db.commit()
    result = getattr(current, operation)(old.raw_token)
    assert result is (None if operation == 'renew' else False)
    assert current.lookup(new.raw_token).user_id == user.id


def test_postgres_locking_cleanup_and_timeouts_without_connecting(settings):
    db = Mock()
    db.get_bind.return_value.dialect.name = 'postgresql'
    db.scalar.return_value = None
    current = service(db, settings)
    assert current.renew('a' * 43) is None
    settings_sql = str(db.execute.call_args.args[0])
    assert "set_config('lock_timeout', '2000ms', true)" in settings_sql
    assert "set_config('statement_timeout', '5000ms', true)" in settings_sql
    lock_sql = str(db.scalar.call_args.args[0].compile(dialect=postgresql.dialect()))
    assert 'FOR UPDATE' in lock_sql
    db.scalars.return_value.all.return_value = []
    assert current.cleanup_expired(batch_size=7) == 0
    statement = db.scalars.call_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    assert 'FOR UPDATE SKIP LOCKED' in str(compiled)
    assert 7 in compiled.params.values()
    assert 'now()' not in str(compiled)


@pytest.mark.parametrize('operation', ['renew', 'revoke', 'rotate_for_login', 'cleanup_expired'])
def test_write_lock_failure_rolls_back_safely(db, settings, operation):
    current = service(db, settings)
    issued = current.create()
    db.commit()
    with patch.object(current, '_prepare_write', side_effect=SQLAlchemyError('private lock error')), \
            patch.object(db, 'rollback', wraps=db.rollback) as rollback:
        with pytest.raises(SessionStorageError) as error:
            if operation == 'cleanup_expired':
                current.cleanup_expired()
            elif operation == 'rotate_for_login':
                current.rotate_for_login(issued.raw_token, 1)
            else:
                getattr(current, operation)(issued.raw_token)
        rollback.assert_called_once()
    assert 'private lock error' not in ''.join(traceback.format_exception(error.value))
    assert current.lookup(issued.raw_token) is not None


def test_cleanup_only_removes_expired_sessions_and_preserves_other_tables(db, settings, user):
    from app.models import JobApplication
    current = service(db, settings)
    expired_anonymous = current.create()
    idle_expired = current.create(user.id)
    live = service(db, settings, NOW + timedelta(minutes=29)).create(user.id)
    db.add(JobApplication(company_name='Preserved', job_title='Engineer', user_id=user.id))
    db.add(JobApplication(company_name='Legacy', job_title='Unowned'))
    db.commit()
    assert service(db, settings, NOW + timedelta(minutes=30)).cleanup_expired() == 2
    db.commit()
    assert db.get(AuthSession, expired_anonymous.session_id) is None
    assert db.get(AuthSession, idle_expired.session_id) is None
    assert db.get(AuthSession, live.session_id) is not None
    assert db.scalar(select(func.count()).select_from(User)) == 1
    assert db.scalar(select(func.count()).select_from(JobApplication)) == 2


def test_cleanup_absolute_boundary_and_unexpired_anonymous_session(db, settings):
    expired = service(db, settings).create()
    live = service(db, settings, NOW + timedelta(seconds=1)).create()
    db.commit()
    assert service(db, settings, NOW + timedelta(seconds=899)).cleanup_expired() == 0
    assert service(db, settings, NOW + timedelta(seconds=900)).cleanup_expired() == 1
    db.commit()
    assert db.get(AuthSession, expired.session_id) is None
    assert db.get(AuthSession, live.session_id) is not None


def test_cleanup_is_bounded_idempotent_and_rollbackable(db, settings):
    for _ in range(5):
        service(db, settings).create()
    db.commit()
    current = service(db, settings, NOW + timedelta(minutes=15))
    assert current.cleanup_expired(batch_size=2) == 2
    db.rollback()
    assert db.scalar(select(func.count()).select_from(AuthSession)) == 5
    counts = []
    for _ in range(4):
        counts.append(current.cleanup_expired(batch_size=2))
        db.commit()
    assert counts == [2, 2, 1, 0]


@pytest.mark.parametrize('batch_size', [0, -1, 1001, True, '10', None])
def test_cleanup_rejects_invalid_batch_before_accessing_db(db, settings, batch_size):
    with patch.object(db, 'get_bind') as bind, pytest.raises(ValueError, match='batch size'):
        service(db, settings).cleanup_expired(batch_size)
    bind.assert_not_called()


def test_cleanup_rechecks_after_selection_if_clock_moves_backwards(db, settings):
    issued = service(db, settings).create()
    db.commit()
    times = iter([NOW + timedelta(minutes=15), NOW])
    current = SessionService(db, settings, clock=lambda: next(times))
    assert current.cleanup_expired() == 0
    db.commit()
    assert db.get(AuthSession, issued.session_id) is not None


def test_cleanup_delete_failure_rolls_back(db, settings):
    issued = service(db, settings).create()
    db.commit()
    scalars = db.scalars

    def fail_on_delete(statement, *args, **kwargs):
        if isinstance(statement, Delete):
            raise SQLAlchemyError('private cleanup failure')
        return scalars(statement, *args, **kwargs)

    with patch.object(db, 'scalars', side_effect=fail_on_delete), pytest.raises(SessionStorageError):
        service(db, settings, NOW + timedelta(minutes=15)).cleanup_expired()
    assert db.get(AuthSession, issued.session_id) is not None
