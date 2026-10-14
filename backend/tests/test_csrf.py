from datetime import UTC, datetime, timedelta
import hashlib
import hmac
from unittest.mock import patch

from fastapi import HTTPException
import pytest
from starlette.requests import Request

from app.auth_config import AuthSettings
from app.csrf import create_csrf_token, require_csrf, require_trusted_origin, verify_csrf_token
from app.models import AuthSession

NOW = datetime(2026, 10, 14, 12, tzinfo=UTC)
RAW = 'a' * 43
OTHER = 'b' * 43


@pytest.fixture
def settings():
    return AuthSettings.from_environment({'CSRF_HMAC_SECRET': 'test-only-csrf-secret-not-production-12345'})


def record(token=RAW):
    return AuthSession(id=1, token_hash=hashlib.sha256(token.encode()).digest(),
                       created_at=NOW, last_activity_at=NOW,
                       expires_at=NOW + timedelta(hours=8), idle_expires_at=NOW + timedelta(minutes=30))


def request(settings, *, method='POST', origin='http://localhost:5173', referer=None,
            content_type='application/json', csrf=True, token=RAW, extra_headers=()):
    headers = []
    if origin is not None:
        headers.append(('origin', origin))
    if referer is not None:
        headers.append(('referer', referer))
    if content_type is not None:
        headers.append(('content-type', content_type))
    if token is not None:
        headers.append(('cookie', f'{settings.session_cookie_name}={token}'))
    if csrf is not None:
        headers.append(('x-csrf-token', create_csrf_token(token or RAW, settings) if csrf is True else csrf))
    headers.extend(extra_headers)
    return Request({'type': 'http', 'method': method, 'path': '/test-only',
                    'headers': [(key.encode(), value.encode()) for key, value in headers]})


def assert_rejected(settings, status=403, session=None, **kwargs):
    with pytest.raises(HTTPException) as error:
        require_csrf(request(settings, **kwargs), record() if session is None else session, settings, now=NOW)
    assert error.value.status_code == status
    assert error.value.headers['Cache-Control'] == 'no-store'
    assert RAW not in str(error.value.detail)
    assert settings.csrf_hmac_secret.decode() not in str(error.value.detail)


def test_hmac_formula_and_constant_time_comparison(settings):
    expected = hmac.new(settings.csrf_hmac_secret, b'csrf:v1:' + RAW.encode(), hashlib.sha256).hexdigest()
    assert create_csrf_token(RAW, settings) == expected
    with patch('app.csrf.hmac.compare_digest', wraps=hmac.compare_digest) as compare:
        assert verify_csrf_token(RAW, expected, settings)
        compare.assert_called_once_with(expected, expected)
    assert create_csrf_token(RAW, settings) != create_csrf_token(OTHER, settings)


@pytest.mark.parametrize('submitted', [None, '', 'wrong', '0' * 64, 'x' * 10000, '🔒' * 64])
def test_invalid_tokens_return_false(settings, submitted):
    assert not verify_csrf_token(RAW, submitted, settings)


def test_missing_or_invalid_raw_session_token(settings):
    csrf = create_csrf_token(RAW, settings)
    assert not verify_csrf_token(None, csrf, settings)
    assert not verify_csrf_token('malformed', csrf, settings)
    with pytest.raises(ValueError, match='valid session token'):
        create_csrf_token('malformed', settings)


def test_cross_session_and_changed_secret_fail(settings):
    csrf = create_csrf_token(RAW, settings)
    assert not verify_csrf_token(OTHER, csrf, settings)
    changed = AuthSettings.from_environment({'CSRF_HMAC_SECRET': 'a different test-only secret-1234567890'})
    assert not verify_csrf_token(RAW, csrf, changed)
    assert_rejected(settings, csrf=create_csrf_token(OTHER, settings))
    # Even a matching cookie/header cannot be paired with another database row.
    assert_rejected(settings, session=record(OTHER))


@pytest.mark.parametrize('method', ['POST', 'PUT', 'PATCH', 'DELETE'])
def test_valid_unsafe_json_request(settings, method):
    session = record()
    require_csrf(request(settings, method=method), session, settings, now=NOW)
    assert session.last_activity_at == NOW  # Guard has no renewal/storage side effects.


@pytest.mark.parametrize('origin', ['http://localhost:5173', 'http://127.0.0.1:5173', 'http://LOCALHOST:5173'])
def test_trusted_origins(settings, origin):
    require_csrf(request(settings, origin=origin), record(), settings, now=NOW)


@pytest.mark.parametrize('origin', [
    'http://localhost:5174', 'https://localhost:5173', 'http://localhost:5173.evil.test',
    'http://evil.test', 'null', '', '*', 'http://localhost:5173/',
    'http://localhost:5173/path', 'http://localhost:5173?secret=1',
    'http://user:secret@localhost:5173', 'http://localhost:5173 http://evil.test',
])
def test_untrusted_origin_cannot_use_trusted_referer(settings, origin):
    assert_rejected(settings, origin=origin, referer='http://localhost:5173/applications')


def test_missing_origin_with_valid_referer_fallback(settings):
    require_csrf(request(settings, origin=None, referer='http://localhost:5173/applications?q=value'), record(), settings, now=NOW)


@pytest.mark.parametrize('referer', [
    None, '', 'null', '/applications', '//localhost:5173/applications',
    'http://evil.test/applications', 'http://localhost:5174/applications',
    'http://user:secret@localhost:5173/applications', 'http://localhost:5173/applications#fragment',
    'http://localhost:5173\\@evil.test/path', 'http://localhost:5173/with space',
])
def test_missing_origin_fails_without_strict_referer(settings, referer):
    assert_rejected(settings, origin=None, referer=referer)


@pytest.mark.parametrize('content_type', [None, '', 'text/plain', 'text/html',
    'application/x-www-form-urlencoded', 'multipart/form-data', 'application/problem+json',
    'application/json; charset=latin-1', 'application/json; boundary=anything'])
def test_unsupported_content_type(settings, content_type):
    assert_rejected(settings, status=415, content_type=content_type)


@pytest.mark.parametrize('content_type', ['application/json', 'Application/JSON',
    'application/json; charset=UTF-8', 'application/json; charset="utf-8"'])
def test_json_utf8_content_types(settings, content_type):
    require_csrf(request(settings, content_type=content_type), record(), settings, now=NOW)


@pytest.mark.parametrize('header,value,status', [
    ('origin', 'http://localhost:5173', 403),
    ('x-csrf-token', '0' * 64, 403),
    ('content-type', 'application/json', 415),
])
def test_duplicate_security_headers_fail_closed(settings, header, value, status):
    assert_rejected(settings, status=status, extra_headers=[(header, value)])


def test_duplicate_referer_fails_closed(settings):
    assert_rejected(settings, origin=None, referer='http://localhost:5173/',
                    extra_headers=[('referer', 'http://localhost:5173/')])


@pytest.mark.parametrize('submitted', [None, '', '0' * 64])
def test_missing_and_invalid_header_rejected(settings, submitted):
    assert_rejected(settings, csrf=submitted)


@pytest.mark.parametrize('attribute', ['expires_at', 'idle_expires_at'])
def test_expired_session_rejects_otherwise_valid_csrf(settings, attribute):
    session = record()
    setattr(session, attribute, NOW)
    assert_rejected(settings, session=session)


def test_missing_session_or_cookie_rejected(settings):
    with pytest.raises(HTTPException) as error:
        require_csrf(request(settings), None, settings, now=NOW)
    assert error.value.status_code == 403
    assert_rejected(settings, token=None)


@pytest.mark.parametrize('method', ['GET', 'HEAD', 'OPTIONS'])
def test_safe_methods_are_not_csrf_gated(settings, method):
    require_csrf(request(settings, method=method, token=None, origin=None, content_type=None, csrf=None),
                 None, settings, now=NOW)


def test_origin_guard_available_for_future_bootstrap_endpoint(settings):
    require_trusted_origin(request(settings, method='GET'), settings)
    with pytest.raises(HTTPException):
        require_trusted_origin(request(settings, method='GET', origin='http://evil.test'), settings)
