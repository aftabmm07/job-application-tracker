"""Lease lifecycle tests use private synthetic receipts and mocked activity hooks."""
from datetime import UTC, datetime
import json
import os
import time
from unittest.mock import Mock

import pytest

from test_browser_database_validation import browser  # noqa: F401 - shared isolated fixture
from scripts.browser_database_validation import validate_browser_database
from scripts.browser_operation import browser_operation
from scripts import postgres_test_environment as environment
from scripts import postgres_test_provenance as provenance
from scripts.postgres_test_harness import UnsafeTestTarget


@pytest.fixture
def ready(browser, monkeypatch):
    target, receipt, path = browser
    receipt.update(state='validated', operation_nonce=None, operator=None)
    path.write_text(json.dumps(receipt))
    monkeypatch.delenv('PG_TEST_OPERATION_NONCE')
    return target, receipt, path


def options(ready, **changes):
    target, _, _ = ready
    result = dict(approved_run=target.run_id, approved_database=target.database('api'),
                  stop=Mock(), is_stopped=Mock(return_value=True))
    result.update(changes)
    return result


def test_acquire_validate_release_and_no_reuse(ready):
    _, _, path = ready
    args = options(ready)
    def stop(timeout):
        assert timeout == 5
        assert provenance.read_receipt(path)['state'] == 'browser-integration'
        with pytest.raises(UnsafeTestTarget):
            with provenance.locked_receipt(path):
                pass
    args['stop'].side_effect = stop
    with browser_operation(**args) as deadline:
        assert time.monotonic() < deadline
        validate_browser_database()
        for action in ('test', 'cleanup'):
            with pytest.raises(UnsafeTestTarget):
                environment.operate(action, path)
    args['stop'].assert_called_once_with(timeout=5)
    args['is_stopped'].assert_called_once_with()
    final = provenance.read_receipt(path)
    assert final['state'] == 'browser-complete'
    assert final['operation_nonce'] is None
    assert 'PG_TEST_OPERATION_NONCE' not in os.environ
    for action in ('test', 'cleanup'):
        with pytest.raises(UnsafeTestTarget):
            environment.operate(action, path)
    with pytest.raises(UnsafeTestTarget):
        with browser_operation(**options(ready)):
            pytest.fail('Reused lease')


@pytest.mark.parametrize('changes', [
    {'approved_run': 'wrong'}, {'approved_database': 'jobtracker_db'},
    {'seconds': 0}, {'seconds': 901}, {'seconds': True}, {'seconds': '300'},
    {'stop': None}, {'is_stopped': None},
])
def test_invalid_acquisition_leaves_receipt_unchanged(ready, changes):
    _, _, path = ready
    before = path.read_bytes()
    with pytest.raises(UnsafeTestTarget):
        with browser_operation(**options(ready, **changes)):
            pytest.fail('Invalid acquisition')
    assert path.read_bytes() == before


@pytest.mark.parametrize('state', ['testing', 'cleaning', 'browser-integration', 'tested', 'failed', 'created'])
def test_conflicting_state(ready, state):
    _, receipt, path = ready
    receipt['state'] = state
    path.write_text(json.dumps(receipt))
    with pytest.raises(UnsafeTestTarget):
        with browser_operation(**options(ready)):
            pytest.fail('Conflicting operation')


def test_held_lock_rejects_browser(ready):
    _, _, path = ready
    with provenance.locked_receipt(path):
        with pytest.raises(UnsafeTestTarget):
            with browser_operation(**options(ready)):
                pytest.fail('Lock conflict')


@pytest.mark.parametrize('failure', ['raises', 'still_running', 'interrupt'])
def test_shutdown_failure_quarantines_receipt(ready, failure):
    _, _, path = ready
    args = options(ready)
    if failure == 'raises': args['stop'].side_effect = TimeoutError('synthetic secret')
    if failure == 'interrupt': args['stop'].side_effect = KeyboardInterrupt()
    if failure == 'still_running': args['is_stopped'].return_value = False
    with pytest.raises(UnsafeTestTarget, match='shutdown failed') as error:
        with browser_operation(**args):
            pass
    assert 'synthetic secret' not in str(error.value)
    receipt = provenance.read_receipt(path)
    assert receipt['state'] == 'failed' and receipt['operation_nonce']
    with pytest.raises(UnsafeTestTarget):
        environment.operate('cleanup', path)


def test_body_failure_stops_before_marking_failed(ready):
    args = options(ready)
    with pytest.raises(ValueError):
        with browser_operation(**args):
            raise ValueError('synthetic')
    args['stop'].assert_called_once_with(timeout=5)
    assert provenance.read_receipt(ready[2])['state'] == 'failed'


@pytest.mark.parametrize('clock', ['wall', 'monotonic'])
def test_expiry_denies_validation_and_stops_on_exit(ready, monkeypatch, clock):
    args = options(ready, seconds=1)
    with pytest.raises(UnsafeTestTarget):
        with browser_operation(**args):
            if clock == 'wall':
                now = time.time()
                monkeypatch.setattr(provenance.time, 'time', lambda: now + 2)
            else:
                now = time.monotonic()
                monkeypatch.setattr(provenance.time, 'monotonic', lambda: now + 2)
            with pytest.raises(UnsafeTestTarget):
                validate_browser_database()
    args['stop'].assert_called_once_with(timeout=5)
    assert provenance.read_receipt(ready[2])['state'] == 'failed'


def test_expired_receipt_cannot_acquire(ready):
    _, receipt, path = ready
    receipt['issued_at'] -= provenance.TTL_SECONDS + 1
    path.write_text(json.dumps(receipt))
    with pytest.raises(UnsafeTestTarget):
        with browser_operation(**options(ready)):
            pytest.fail('Expired receipt')


def test_no_approval_no_acquisition(ready, monkeypatch):
    monkeypatch.delenv('APPROVE_BROWSER_INTEGRATION')
    with pytest.raises(UnsafeTestTarget):
        with browser_operation(**options(ready)):
            pytest.fail('Unapproved')


def test_write_failure_cannot_start_activity(ready, monkeypatch):
    from scripts import browser_operation as lifecycle
    monkeypatch.setattr(lifecycle, 'write_receipt', Mock(side_effect=OSError('synthetic write failure')))
    args = options(ready)
    with pytest.raises(OSError):
        with browser_operation(**args):
            pytest.fail('Activity must not start')
    args['stop'].assert_not_called()


def test_lease_cannot_outlive_receipt(ready):
    _, receipt, path = ready
    receipt['issued_at'] = time.time() - provenance.TTL_SECONDS + 30
    receipt['container_created'] = datetime.fromtimestamp(
        receipt['issued_at'], UTC).isoformat()
    path.write_text(json.dumps(receipt))
    with browser_operation(**options(ready, seconds=900)):
        current = provenance.read_receipt(path)
        assert current['browser_expires_at'] <= current['issued_at'] + provenance.TTL_SECONDS
        assert 0 < current['browser_expires_at'] - time.time() <= 30
