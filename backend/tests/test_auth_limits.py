from concurrent.futures import ThreadPoolExecutor
from fastapi import HTTPException
import pytest

from app.auth_limits import AuthRateLimiter


def test_limit_and_window_expiry():
    now = [0.0]
    limiter = AuthRateLimiter(clock=lambda: now[0])
    rule = [('login', 'person@example.com', 2, 60)]
    limiter.check(rule)
    limiter.check(rule)
    with pytest.raises(HTTPException) as error:
        limiter.check(rule)
    assert error.value.status_code == 429
    assert error.value.headers['Retry-After'] == '60'
    assert error.value.headers['Cache-Control'] == 'no-store'
    assert 'person@example.com' not in repr(limiter.entries)
    now[0] = 60
    limiter.check(rule)


def test_key_bound_fails_closed_and_cleanup_releases_space():
    now = [0.0]
    limiter = AuthRateLimiter(clock=lambda: now[0], max_keys=1)
    limiter.check([('scope', 'one', 1, 60)])
    with pytest.raises(HTTPException):
        limiter.check([('scope', 'two', 1, 60)])
    assert len(limiter.entries) == 1
    now[0] = 61
    limiter.check([('scope', 'two', 1, 60)])
    assert len(limiter.entries) == 1


def test_concurrent_attempts_do_not_bypass_limit():
    limiter = AuthRateLimiter()
    def attempt(_):
        try:
            limiter.check([('login', 'same-account', 5, 60)])
            return True
        except HTTPException:
            return False
    with ThreadPoolExecutor(max_workers=10) as executor:
        assert sum(executor.map(attempt, range(30))) == 5
