import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
from types import SimpleNamespace
import os
import subprocess
import sys

import pytest

import app.passwords as passwords

PASSWORD = "synthetic password for limit tests"


@pytest.fixture
def limiter(monkeypatch):
    gate = passwords.PasswordWorkLimiter(2)
    monkeypatch.setattr(passwords, "get_password_work_limiter", lambda: gate)
    return gate


def call(operation):
    if operation == "hash_password":
        return passwords.hash_password(PASSWORD)
    return getattr(passwords, operation)(PASSWORD, "synthetic stored hash")


def test_saturation_rejects_without_queueing_and_bounds_active_work(limiter, monkeypatch):
    started, finish = Event(), Event()
    lock = Lock()
    active = 0

    def work(*args):
        nonlocal active
        with lock:
            active += 1
            if active == 2:
                started.set()
        try:
            assert finish.wait(5)
            return "synthetic hash"
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(passwords, "_password_hash", SimpleNamespace(hash=work))
    with ThreadPoolExecutor(max_workers=3) as workers:
        first = [workers.submit(passwords.hash_password, PASSWORD) for _ in range(2)]
        try:
            assert started.wait(5)
            rejected = workers.submit(passwords.hash_password, PASSWORD)
            with pytest.raises(passwords.PasswordWorkBusy):
                rejected.result(timeout=1)
            assert active == 2
        finally:
            finish.set()
        assert [future.result(timeout=5) for future in first] == ["synthetic hash"] * 2
    assert passwords.hash_password(PASSWORD) == "synthetic hash"


@pytest.mark.parametrize("operation", ["hash_password", "verify_password", "verify_and_upgrade_password"])
def test_all_expensive_helpers_share_the_same_gate(limiter, operation):
    with limiter.acquire(), limiter.acquire():
        with pytest.raises(passwords.PasswordWorkBusy) as error:
            call(operation)
    assert PASSWORD not in str(error.value)


def test_real_hashing_and_verification_below_capacity(limiter):
    encoded = passwords.hash_password(PASSWORD)
    assert encoded.startswith("$argon2id$v=19$m=65536,t=3,p=4$")
    assert passwords.verify_password(PASSWORD, encoded)
    assert passwords.verify_and_upgrade_password(PASSWORD, encoded) == (True, None)


@pytest.mark.parametrize("operation, method", [
    ("hash_password", "hash"), ("verify_password", "verify"),
    ("verify_and_upgrade_password", "verify_and_update"),
])
@pytest.mark.parametrize("error_type", [RuntimeError, asyncio.CancelledError])
def test_permits_are_released_on_failure_or_cancellation(limiter, monkeypatch, operation, method, error_type):
    def fail(*args):
        raise error_type("synthetic private marker")

    monkeypatch.setattr(passwords, "_password_hash", SimpleNamespace(**{method: fail}))
    with pytest.raises(error_type):
        call(operation)
    # Both slots must be available after the failure, including BaseException.
    with limiter.acquire(), limiter.acquire():
        pass


def test_cancelled_waiter_cannot_release_a_running_workers_slot(monkeypatch):
    gate = passwords.PasswordWorkLimiter(1)
    monkeypatch.setattr(passwords, "get_password_work_limiter", lambda: gate)
    started, finish = Event(), Event()

    def work(*args):
        started.set()
        assert finish.wait(5)
        return "synthetic hash"

    monkeypatch.setattr(passwords, "_password_hash", SimpleNamespace(hash=work))
    with ThreadPoolExecutor(max_workers=1) as workers:
        worker = workers.submit(passwords.hash_password, PASSWORD)
        try:
            assert started.wait(5)

            async def cancel_waiter():
                waiter = asyncio.wrap_future(worker)
                waiter.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await waiter
                with pytest.raises(passwords.PasswordWorkBusy):
                    passwords.hash_password(PASSWORD)

            asyncio.run(cancel_waiter())
        finally:
            finish.set()
        assert worker.result(timeout=5) == "synthetic hash"
    assert passwords.hash_password(PASSWORD) == "synthetic hash"


def test_configured_capacity_is_applied_on_import():
    environment = os.environ.copy()
    environment.update(APP_ENV="development", PASSWORD_HASH_MAX_CONCURRENCY="1")
    code = """
from unittest.mock import patch
with patch('dotenv.load_dotenv'):
    from app.passwords import get_password_work_limiter, PasswordWorkBusy
gate = get_password_work_limiter()
with gate.acquire():
    try:
        with gate.acquire():
            raise AssertionError('Configured capacity was ignored')
    except PasswordWorkBusy:
        pass
with gate.acquire():
    pass
"""
    result = subprocess.run([sys.executable, "-c", code], env=environment,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
