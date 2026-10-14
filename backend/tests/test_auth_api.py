from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.auth_config import AuthSettings, get_auth_settings
from app.auth_limits import AuthRateLimiter, get_auth_limiter
from app.main import app, get_db
from app.models import AuthSession, Base, User
from app.passwords import hash_password, verify_password
from scripts.activate_development_user import activate_user

PASSWORD = 'a safe test password with spaces'
EMAIL = 'person@example.com'
ORIGIN = 'http://localhost:5173'


@pytest.fixture(scope='module')
def encoded_password():
    return hash_password(PASSWORD)


@pytest.fixture
def ctx(encoded_password):
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    settings = AuthSettings.from_environment({})
    limiter = AuthRateLimiter()
    with Session(engine) as db:
        def override_db():
            yield db
        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_auth_settings] = lambda: settings
        app.dependency_overrides[get_auth_limiter] = lambda: limiter
        try:
            with TestClient(app) as client:
                yield SimpleNamespace(db=db, client=client, settings=settings, limiter=limiter,
                                      password_hash=encoded_password, csrf=None)
        finally:
            for dependency in (get_db, get_auth_settings, get_auth_limiter):
                app.dependency_overrides.pop(dependency, None)
    engine.dispose()


def bootstrap(ctx):
    response = ctx.client.get('/auth/csrf', headers={'Origin': ORIGIN})
    assert response.status_code == 200
    ctx.csrf = response.json()['csrf_token']
    return response


def headers(ctx):
    values = {'Origin': ORIGIN, 'Content-Type': 'application/json'}
    if ctx.csrf is not None:
        values['X-CSRF-Token'] = ctx.csrf
    return values


def post(ctx, path, data=None):
    return ctx.client.post(path, json=data if data is not None else {'email': EMAIL, 'password': PASSWORD}, headers=headers(ctx))


def add_user(ctx, *, active=True, email=EMAIL):
    user = User(email=email, password_hash=ctx.password_hash, is_active=active,
                email_verified_at=datetime.now(UTC) if active else None)
    ctx.db.add(user)
    ctx.db.commit()
    return user.id


def sign_in(ctx):
    add_user(ctx)
    bootstrap(ctx)
    response = post(ctx, '/auth/login')
    assert response.status_code == 200
    bootstrap(ctx)  # Rotation changes the CSRF token.
    return response


def count(ctx, model):
    return ctx.db.scalar(select(func.count()).select_from(model))


def expire_record(ctx):
    row = ctx.db.scalar(select(AuthSession))
    row.created_at = datetime.now(UTC) - timedelta(days=2)
    row.last_activity_at = row.created_at
    row.expires_at = datetime.now(UTC) - timedelta(days=1)
    row.idle_expires_at = row.expires_at
    ctx.db.commit()


def test_bootstrap_reuses_session_and_exposes_only_csrf(ctx):
    first = bootstrap(ctx)
    raw = ctx.client.cookies.get('session')
    assert set(first.json()) == {'csrf_token'}
    assert raw not in first.text
    row = ctx.db.scalar(select(AuthSession))
    last_activity = row.last_activity_at
    second = bootstrap(ctx)
    assert second.json() == first.json()
    assert 'set-cookie' not in second.headers
    assert count(ctx, AuthSession) == 1
    assert ctx.db.scalar(select(AuthSession)).last_activity_at == last_activity
    assert first.headers['cache-control'] == second.headers['cache-control'] == 'no-store'
    assert 'HttpOnly' in first.headers['set-cookie']


def test_bootstrap_replaces_unknown_cookie(ctx):
    ctx.client.cookies.set('session', 'x' * 43)
    bootstrap(ctx)
    assert count(ctx, AuthSession) == 1
    assert ctx.db.scalar(select(AuthSession)).user_id is None


def test_bootstrap_replaces_expired_session(ctx):
    bootstrap(ctx)
    old = ctx.db.scalar(select(AuthSession)).id
    expire_record(ctx)
    response = bootstrap(ctx)
    assert response.headers.get('set-cookie')
    assert count(ctx, AuthSession) == 2  # Expired-row cleanup is separate.
    assert ctx.db.scalar(select(AuthSession).where(AuthSession.id != old)).user_id is None


@pytest.mark.parametrize('request_headers', [{}, {'Origin': 'http://evil.example'}, {'Origin': 'null'}])
def test_bootstrap_rejects_untrusted_or_missing_source(ctx, request_headers):
    response = ctx.client.get('/auth/csrf', headers=request_headers)
    assert response.status_code == 403
    assert count(ctx, AuthSession) == 0
    assert response.headers['cache-control'] == 'no-store'


def test_bootstrap_accepts_strict_referer_fallback(ctx):
    response = ctx.client.get('/auth/csrf', headers={'Referer': ORIGIN + '/applications'})
    assert response.status_code == 200


def test_registration_normalizes_hashes_and_does_not_authenticate(ctx):
    bootstrap(ctx)
    response = post(ctx, '/auth/register', {'email': '  Person@Example.COM  ', 'password': PASSWORD})
    assert response.status_code == 202
    user = ctx.db.scalar(select(User))
    assert user.email == EMAIL
    assert not user.is_active and user.email_verified_at is None
    assert user.password_hash.startswith('$argon2id$')
    assert verify_password(PASSWORD, user.password_hash)
    assert 'set-cookie' not in response.headers
    assert ctx.db.scalar(select(AuthSession)).user_id is None
    assert ctx.client.get('/auth/me').status_code == 401
    assert PASSWORD not in response.text


def test_duplicate_registration_is_indistinguishable_and_does_not_overwrite(ctx):
    bootstrap(ctx)
    first = post(ctx, '/auth/register')
    original = ctx.db.scalar(select(User)).password_hash
    duplicate = post(ctx, '/auth/register', {'email': EMAIL.upper(), 'password': PASSWORD + ' different'})
    assert duplicate.status_code == first.status_code == 202
    assert duplicate.json() == first.json()
    assert count(ctx, User) == 1
    assert ctx.db.scalar(select(User)).password_hash == original


@pytest.mark.parametrize('payload', [
    {'email': 'invalid', 'password': PASSWORD},
    {'email': 'Person <person@example.com>', 'password': PASSWORD},
    {'email': EMAIL, 'password': 'short-secret'},
    {'email': EMAIL, 'password': 'x' * 129},
    {'email': EMAIL, 'password': 123456789012345},
    {'email': EMAIL}, {'password': PASSWORD},
    {'email': EMAIL, 'password': PASSWORD, 'is_active': True},
])
def test_invalid_credentials_are_sanitized(ctx, payload, caplog):
    bootstrap(ctx)
    response = post(ctx, '/auth/register', payload)
    assert response.status_code == 422
    assert response.json() == {'detail': 'Invalid authentication request'}
    assert str(payload.get('password', PASSWORD)) not in response.text
    assert PASSWORD not in caplog.text
    assert count(ctx, User) == 0
    assert response.headers['cache-control'] == 'no-store'


def test_unicode_and_exact_password_preserved(ctx):
    bootstrap(ctx)
    password = '  密碼🔒 café passphrase  '
    response = post(ctx, '/auth/register', {'email': EMAIL, 'password': password})
    assert response.status_code == 202
    encoded = ctx.db.scalar(select(User)).password_hash
    assert verify_password(password, encoded)
    assert not verify_password(password.strip(), encoded)


def test_registration_unique_race_returns_generic_202(ctx):
    bootstrap(ctx)
    real_commit = ctx.db.commit
    def concurrent_insert():
        ctx.db.rollback()  # Competing request wins before our insert commits.
        ctx.db.add(User(email=EMAIL, password_hash=ctx.password_hash))
        real_commit()
        raise IntegrityError('synthetic uniqueness race', {}, Exception('private detail'))
    with patch.object(ctx.db, 'commit', side_effect=concurrent_insert):
        response = post(ctx, '/auth/register')
    assert response.status_code == 202
    assert count(ctx, User) == 1
    assert not ctx.db.scalar(select(User)).is_active


def test_registration_unrelated_integrity_error_is_not_reported_as_success(ctx):
    bootstrap(ctx)
    with patch.object(ctx.db, 'commit', side_effect=IntegrityError('other constraint', {}, Exception(PASSWORD))):
        response = post(ctx, '/auth/register')
    assert response.status_code == 503
    assert PASSWORD not in response.text
    assert count(ctx, User) == 0


def test_login_rotates_session_and_safe_identity(ctx):
    user_id = add_user(ctx)
    bootstrap(ctx)
    old_raw = ctx.client.cookies.get('session')
    old_csrf = ctx.csrf
    response = post(ctx, '/auth/login')
    assert response.status_code == 200
    assert response.json() == {'id': user_id, 'email': EMAIL}
    new_raw = ctx.client.cookies.get('session')
    assert new_raw != old_raw
    assert new_raw not in response.text and old_raw not in response.text
    assert count(ctx, AuthSession) == 1
    assert ctx.db.scalar(select(AuthSession)).user_id == user_id
    assert ctx.client.get('/auth/me').json() == response.json()
    bootstrap(ctx)
    assert ctx.csrf != old_csrf
    ctx.client.cookies.set('session', old_raw, domain='testserver.local', path='/')
    assert ctx.client.get('/auth/me').status_code == 401


@pytest.mark.parametrize('kind', ['unknown', 'wrong', 'inactive'])
def test_login_failures_are_generic_and_do_not_rotate(ctx, kind):
    if kind != 'unknown':
        add_user(ctx, active=kind != 'inactive')
    bootstrap(ctx)
    old_raw = ctx.client.cookies.get('session')
    response = post(ctx, '/auth/login', {'email': EMAIL, 'password': PASSWORD + ' wrong' if kind == 'wrong' else PASSWORD})
    assert response.status_code == 401
    assert response.json() == {'detail': 'Invalid credentials'}
    assert 'set-cookie' not in response.headers
    assert ctx.client.cookies.get('session') == old_raw
    assert ctx.db.scalar(select(AuthSession)).user_id is None


def test_unknown_user_performs_password_verification(ctx):
    from app.auth_routes import verify_and_upgrade_password
    bootstrap(ctx)
    with patch('app.auth_routes.verify_and_upgrade_password', wraps=verify_and_upgrade_password) as verify:
        assert post(ctx, '/auth/login').status_code == 401
    verify.assert_called_once()


def test_login_requires_anonymous_session(ctx):
    add_user(ctx)
    response = post(ctx, '/auth/login')
    assert response.status_code == 403
    bootstrap(ctx)
    assert post(ctx, '/auth/login').status_code == 200
    bootstrap(ctx)
    assert post(ctx, '/auth/login').status_code == 403


def test_me_rejects_anonymous_missing_and_revoked_sessions(ctx):
    assert ctx.client.get('/auth/me').status_code == 401
    bootstrap(ctx)
    assert ctx.client.get('/auth/me').status_code == 401
    ctx.db.query(AuthSession).delete()
    ctx.db.commit()
    assert ctx.client.get('/auth/me').status_code == 401


def test_me_rejects_expired_and_disabled_accounts(ctx):
    sign_in(ctx)
    expire_record(ctx)
    assert ctx.client.get('/auth/me').status_code == 401
    ctx.client.cookies.clear()
    bootstrap(ctx)
    assert post(ctx, '/auth/login').status_code == 200
    user = ctx.db.scalar(select(User))
    user.is_active = False
    ctx.db.commit()
    assert ctx.client.get('/auth/me').status_code == 401


def test_logout_revokes_before_clearing_cookie(ctx):
    sign_in(ctx)
    response = post(ctx, '/auth/logout', {})
    assert response.status_code == 204 and response.content == b''
    assert 'Max-Age=0' in response.headers['set-cookie']
    assert count(ctx, AuthSession) == 0
    assert ctx.client.get('/auth/me').status_code == 401
    assert post(ctx, '/auth/logout', {}).status_code == 403  # Strict invalid-session behavior.


def test_anonymous_logout_and_expired_logout(ctx):
    bootstrap(ctx)
    assert post(ctx, '/auth/logout', {}).status_code == 204
    bootstrap(ctx)
    expire_record(ctx)
    response = post(ctx, '/auth/logout', {})
    assert response.status_code == 403 and 'set-cookie' not in response.headers


@pytest.mark.parametrize('path', ['/auth/register', '/auth/login', '/auth/logout'])
@pytest.mark.parametrize('bad', ['csrf', 'origin', 'missing_origin', 'content_type'])
def test_unsafe_endpoints_require_security_checks(ctx, path, bad):
    bootstrap(ctx)
    request_headers = headers(ctx)
    if bad == 'csrf':
        request_headers.pop('X-CSRF-Token')
    elif bad == 'origin':
        request_headers['Origin'] = 'http://evil.example'
    elif bad == 'missing_origin':
        request_headers.pop('Origin')
    else:
        request_headers['Content-Type'] = 'text/plain'
    response = ctx.client.post(path, json={'email': EMAIL, 'password': PASSWORD}, headers=request_headers)
    assert response.status_code == (415 if bad == 'content_type' else 403)
    assert response.headers['cache-control'] == 'no-store'
    assert count(ctx, User) == 0 and count(ctx, AuthSession) == 1


@pytest.mark.parametrize('path', ['/auth/register', '/auth/login', '/auth/logout'])
def test_database_commit_failure_rolls_back_and_never_sets_cookie(ctx, path):
    if path != '/auth/register':
        add_user(ctx)
    bootstrap(ctx)
    original_token = ctx.client.cookies.get('session')
    with patch.object(ctx.db, 'commit', side_effect=SQLAlchemyError(PASSWORD)):
        response = post(ctx, path)
    assert response.status_code == 503
    assert PASSWORD not in response.text
    assert 'set-cookie' not in response.headers
    assert count(ctx, AuthSession) == 1
    assert ctx.db.scalar(select(AuthSession)).user_id is None
    assert ctx.client.cookies.get('session') == original_token
    if path == '/auth/register':
        assert count(ctx, User) == 0


def test_bootstrap_commit_failure_does_not_emit_cookie(ctx):
    with patch.object(ctx.db, 'commit', side_effect=SQLAlchemyError(PASSWORD)):
        response = ctx.client.get('/auth/csrf', headers={'Origin': ORIGIN})
    assert response.status_code == 503
    assert 'set-cookie' not in response.headers
    assert count(ctx, AuthSession) == 0


def test_read_failure_is_generic_503(ctx):
    bootstrap(ctx)
    with patch.object(ctx.db, 'scalar', side_effect=SQLAlchemyError(PASSWORD)):
        response = ctx.client.get('/auth/me')
    assert response.status_code == 503
    assert PASSWORD not in response.text


def test_login_and_registration_throttling(ctx):
    bootstrap(ctx)
    for _ in range(10):
        assert post(ctx, '/auth/login').status_code == 401
    limited = post(ctx, '/auth/login')
    assert limited.status_code == 429 and int(limited.headers['retry-after']) > 0
    assert limited.headers['cache-control'] == 'no-store'


def test_auth_404_405_and_malformed_json_are_no_store(ctx):
    assert ctx.client.get('/auth/not-found').headers['cache-control'] == 'no-store'
    assert ctx.client.get('/auth/login').headers['cache-control'] == 'no-store'
    bootstrap(ctx)
    response = ctx.client.post('/auth/register', content='{"password":"private-malformed', headers=headers(ctx))
    assert response.status_code == 422
    assert 'private-malformed' not in response.text
    assert response.headers['cache-control'] == 'no-store'


def test_development_activation_is_private_and_explicit(ctx):
    user_id = add_user(ctx, active=False)
    activate_user(ctx.db, user_id, ctx.settings, 'jobtracker_auth_fresh_9b2', confirmed=True)
    user = ctx.db.get(User, user_id)
    assert user.is_active and user.email_verified_at is not None
    assert ctx.client.post('/auth/activate').status_code == 404


@pytest.mark.parametrize('target,confirmed,production', [
    ('jobtracker_db', True, False), ('production', True, False),
    ('jobtracker_auth_fresh_9b2', False, False), ('jobtracker_auth_fresh_9b2', True, True),
])
def test_activation_guards_before_database_access(target, confirmed, production):
    values = {'APP_ENV': 'production', 'CSRF_HMAC_SECRET': 'test-only-production-key-1234567890123',
              'AUTH_ALLOWED_ORIGINS': 'https://tracker.example.com'} if production else {}
    db = Mock()
    with pytest.raises(RuntimeError):
        activate_user(db, 1, AuthSettings.from_environment(values), target, confirmed=confirmed)
    db.scalar.assert_not_called()


def test_login_hash_upgrade_is_committed_with_rotation(ctx):
    from pwdlib.hashers.argon2 import Argon2Hasher
    user_id = add_user(ctx)
    user = ctx.db.get(User, user_id)
    user.password_hash = Argon2Hasher(memory_cost=8192, time_cost=1, parallelism=1).hash(PASSWORD)
    ctx.db.commit()
    bootstrap(ctx)
    assert post(ctx, '/auth/login').status_code == 200
    from app.passwords import password_hash_needs_upgrade
    assert not password_hash_needs_upgrade(ctx.db.get(User, user_id).password_hash)


def test_failed_login_commit_rolls_back_password_upgrade(ctx):
    from pwdlib.hashers.argon2 import Argon2Hasher
    user_id = add_user(ctx)
    old_hash = Argon2Hasher(memory_cost=8192, time_cost=1, parallelism=1).hash(PASSWORD)
    ctx.db.get(User, user_id).password_hash = old_hash
    ctx.db.commit()
    bootstrap(ctx)
    with patch.object(ctx.db, 'commit', side_effect=SQLAlchemyError('failed commit')):
        response = post(ctx, '/auth/login')
    assert response.status_code == 503
    assert ctx.db.get(User, user_id).password_hash == old_hash
    assert ctx.db.scalar(select(AuthSession)).user_id is None


def test_invalid_csrf_does_not_renew_activity(ctx):
    bootstrap(ctx)
    row = ctx.db.scalar(select(AuthSession))
    before = row.last_activity_at
    response = ctx.client.post('/auth/register', json={'email': EMAIL, 'password': PASSWORD},
                               headers={'Origin': ORIGIN, 'Content-Type': 'application/json', 'X-CSRF-Token': '0' * 64})
    assert response.status_code == 403
    ctx.db.refresh(row)
    assert row.last_activity_at == before


def test_cross_session_csrf_is_rejected_at_endpoint(ctx):
    bootstrap(ctx)
    previous_csrf = ctx.csrf
    ctx.client.cookies.clear()
    bootstrap(ctx)
    ctx.csrf = previous_csrf
    assert post(ctx, '/auth/register').status_code == 403
    assert count(ctx, User) == 0


def test_production_cookies_and_authenticated_bootstrap_reuse(ctx):
    production = AuthSettings.from_environment({
        'APP_ENV': 'production', 'CSRF_HMAC_SECRET': 'test-only-production-key-1234567890123',
        'AUTH_ALLOWED_ORIGINS': 'https://tracker.example.com',
    })
    app.dependency_overrides[get_auth_settings] = lambda: production
    add_user(ctx)
    with TestClient(app, base_url='https://tracker.example.com') as client:
        response = client.get('/auth/csrf', headers={'Origin': 'https://tracker.example.com'})
        assert response.status_code == 200
        cookie = response.headers['set-cookie']
        assert '__Host-session=' in cookie and 'Secure' in cookie and 'HttpOnly' in cookie
        assert 'SameSite=lax' in cookie and 'Path=/' in cookie and 'Domain=' not in cookie
        csrf = response.json()['csrf_token']
        response = client.post('/auth/login', json={'email': EMAIL, 'password': PASSWORD}, headers={
            'Origin': 'https://tracker.example.com', 'Content-Type': 'application/json', 'X-CSRF-Token': csrf,
        })
        assert response.status_code == 200
        assert 'Secure' in response.headers['set-cookie']
        assert client.get('/auth/me').status_code == 200
        response = client.get('/auth/csrf', headers={'Origin': 'https://tracker.example.com'})
        assert 'set-cookie' not in response.headers and count(ctx, AuthSession) == 1


def test_bootstrap_creation_failure_rolls_back(ctx):
    with patch.object(ctx.db, 'flush', side_effect=SQLAlchemyError(PASSWORD)):
        response = ctx.client.get('/auth/csrf', headers={'Origin': ORIGIN})
    assert response.status_code == 503 and 'set-cookie' not in response.headers
    assert count(ctx, AuthSession) == 0


def test_registration_rate_limit_before_password_hashing(ctx):
    bootstrap(ctx)
    from app.auth_routes import hash_password
    with patch('app.auth_routes.hash_password', wraps=hash_password) as hashing:
        for _ in range(10):
            assert post(ctx, '/auth/register').status_code == 202
        assert post(ctx, '/auth/register').status_code == 429
    assert hashing.call_count == 10


def test_bootstrap_rate_limit_does_not_create_additional_sessions(ctx):
    bootstrap(ctx)
    for _ in range(59):
        assert ctx.client.get('/auth/csrf', headers={'Origin': ORIGIN}).status_code == 200
    response = ctx.client.get('/auth/csrf', headers={'Origin': ORIGIN})
    assert response.status_code == 429
    assert count(ctx, AuthSession) == 1


def test_unexpected_auth_error_has_no_secret_details(ctx, caplog):
    bootstrap(ctx)
    with patch('app.auth_routes.hash_password', side_effect=RuntimeError(PASSWORD)):
        response = post(ctx, '/auth/register')
    assert response.status_code == 500
    assert PASSWORD not in response.text and PASSWORD not in caplog.text
    assert response.headers['cache-control'] == 'no-store'
    assert count(ctx, User) == 0


def test_me_commit_failure_does_not_extend_activity(ctx):
    sign_in(ctx)
    record = ctx.db.scalar(select(AuthSession))
    before = record.last_activity_at
    with patch.object(ctx.db, 'commit', side_effect=SQLAlchemyError('failed renewal')):
        response = ctx.client.get('/auth/me')
    assert response.status_code == 503
    ctx.db.refresh(record)
    assert record.last_activity_at == before


def test_explicit_api_database_override_does_not_connect():
    import os
    import subprocess
    import sys
    environment = os.environ.copy()
    environment.update(APP_DATABASE_NAME='jobtracker_auth_fresh_9b2', POSTGRES_PASSWORD='test-only-unused')
    result = subprocess.run([sys.executable, '-c',
        'from app.database import engine; print(engine.url.database)'], env=environment,
        capture_output=True, text=True, timeout=15)
    assert result.returncode == 0
    assert result.stdout.strip() == 'jobtracker_auth_fresh_9b2'
    assert 'test-only-unused' not in result.stdout + result.stderr


@pytest.mark.parametrize('kind', ['register', 'known-login', 'unknown-login'])
def test_password_capacity_failure_is_generic_and_retryable(ctx, monkeypatch, kind, caplog):
    import app.passwords as passwords
    if kind == 'known-login':
        add_user(ctx)
    bootstrap(ctx)
    raw = ctx.client.cookies.get('session')
    gate = passwords.PasswordWorkLimiter(1)
    monkeypatch.setattr(passwords, 'get_password_work_limiter', lambda: gate)
    path = '/auth/register' if kind == 'register' else '/auth/login'
    original = passwords._password_hash
    fake = SimpleNamespace(hash=Mock(), verify_and_update=Mock())
    with patch.object(passwords, '_password_hash', fake), gate.acquire():
        response = post(ctx, path)
    assert response.status_code == 503
    assert response.json() == {'detail': 'Authentication service busy. Please try again later.'}
    assert response.headers['retry-after'] == '1'
    assert response.headers['cache-control'] == 'no-store'
    assert 'set-cookie' not in response.headers
    fake.hash.assert_not_called()
    fake.verify_and_update.assert_not_called()  # Includes the unknown-account dummy path.
    for secret in (PASSWORD, raw, ctx.settings.csrf_hmac_secret.hex()):
        assert secret not in response.text + caplog.text
    assert passwords._password_hash is original
    assert count(ctx, AuthSession) == 1
    assert count(ctx, User) == (1 if kind == 'known-login' else 0)
    # Rejection must leave the anonymous session usable for a subsequent attempt.
    assert post(ctx, path).status_code == (202 if kind == 'register' else 200 if kind == 'known-login' else 401)


def test_unknown_account_password_work_runs_in_worker_thread(ctx, monkeypatch):
    import asyncio
    import app.passwords as passwords
    bootstrap(ctx)
    calls = []

    def verify(*args):
        with pytest.raises(RuntimeError, match='no running event loop'):
            asyncio.get_running_loop()
        calls.append(True)
        return False, None

    monkeypatch.setattr(passwords, '_password_hash', SimpleNamespace(verify_and_update=verify))
    assert post(ctx, '/auth/login').status_code == 401
    assert calls == [True]


@pytest.mark.parametrize('method, path', [
    ('POST', '/auth/register'), ('POST', '/auth/login'), ('POST', '/auth/logout'),
    ('POST', '/applications'), ('PATCH', '/applications/1'), ('DELETE', '/applications/1'),
])
@pytest.mark.parametrize('streamed', [False, True])
def test_large_mutation_rejected_before_json_or_authentication(ctx, method, path, streamed, caplog):
    from app.main import auth_settings
    from app.session_service import SessionService
    body = b'private-body-marker' + b'x' * auth_settings.json_max_body_bytes
    with patch('starlette.requests.Request.json') as parse, patch.object(SessionService, 'lookup') as lookup:
        content = iter([body[:10], body[10:]]) if streamed else body
        response = ctx.client.request(method, path, content=content, headers={'Content-Type': 'application/json'})
    assert response.status_code == 413
    assert response.json() == {'detail': 'Request body too large'}
    assert response.headers['cache-control'] == 'no-store'
    assert 'set-cookie' not in response.headers
    assert 'private-body-marker' not in response.text + caplog.text
    parse.assert_not_called()
    lookup.assert_not_called()
    assert count(ctx, User) == count(ctx, AuthSession) == 0


def test_small_chunked_registration_preserves_csrf_and_json(ctx):
    import json
    bootstrap(ctx)
    body = json.dumps({'email': EMAIL, 'password': PASSWORD}).encode()
    response = ctx.client.post('/auth/register', content=iter([body[:10], body[10:]]), headers=headers(ctx))
    assert response.status_code == 202
    assert count(ctx, User) == 1
    assert not ctx.db.scalar(select(User)).is_active


def test_logout_failed_final_revocation_does_not_clear_cookie(ctx):
    from app.session_service import SessionService
    sign_in(ctx)
    raw = ctx.client.cookies.get('session')
    with patch.object(SessionService, 'revoke', return_value=False):
        response = post(ctx, '/auth/logout', {})
    assert response.status_code == 403
    assert response.json() == {'detail': 'Request security validation failed'}
    assert 'set-cookie' not in response.headers
    assert response.headers['cache-control'] == 'no-store'
    assert ctx.client.cookies.get('session') == raw


@pytest.mark.parametrize('path, expected', [('/auth/login', 401), ('/auth/me', 401), ('/auth/logout', 403)])
def test_auth_expiry_after_lock_does_not_issue_or_clear_cookie(ctx, path, expected):
    from app.session_service import SessionService
    if path == '/auth/login':
        add_user(ctx)
        bootstrap(ctx)
    else:
        sign_in(ctx)
    raw = ctx.client.cookies.get('session')
    now = SessionService._now
    lock = SessionService._lock_session
    after_lock = [None]

    def clock(self):
        return after_lock[0] if after_lock[0] is not None else now(self)

    def delayed_lock(self, digest):
        row = lock(self, digest)
        after_lock[0] = min(row.expires_at, row.idle_expires_at)
        return row

    with patch.object(SessionService, '_now', clock), patch.object(SessionService, '_lock_session', delayed_lock):
        response = ctx.client.get(path) if path == '/auth/me' else post(ctx, path)
    assert response.status_code == expected
    assert 'set-cookie' not in response.headers
    assert response.headers['cache-control'] == 'no-store'
    assert ctx.client.cookies.get('session') == raw
    assert count(ctx, AuthSession) == 1
    assert raw not in response.text and PASSWORD not in response.text
