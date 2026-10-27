"""Offline lease lifecycle only: no serving, engine creation or infrastructure I/O."""
from contextlib import contextmanager
import os
import secrets
import time

from scripts.browser_database_validation import browser_target_configuration, validate_browser_database
from scripts.postgres_test_harness import UnsafeTestTarget
from scripts.postgres_test_provenance import (
    BROWSER_MAX_SECONDS, TTL_SECONDS, locked_receipt, process_identity, write_receipt,
)


@contextmanager
def browser_operation(*, approved_run, approved_database, stop, is_stopped, seconds=300):
    """Hold the exclusive receipt lock until a trusted activity adapter proves exit.

    Future serving must supply bounded stop(timeout=...) and is_stopped() hooks
    before starting any activity, and stop at the deadline. This module starts
    nothing. Unconfirmed shutdown leaves a failed receipt with its nonce intact;
    it cannot authorize tests, cleanup or another browser operation.
    """
    if (type(seconds) is not int or not 1 <= seconds <= BROWSER_MAX_SECONDS
            or not callable(stop) or not callable(is_stopped)):
        raise UnsafeTestTarget('Invalid browser operation lifecycle configuration')
    target, _ = browser_target_configuration()
    if approved_run != target.run_id or approved_database != target.database('api'):
        raise UnsafeTestTarget('Explicit browser run/database approval mismatch')
    path = os.environ['PG_TEST_RECEIPT']
    if os.environ.get('PG_TEST_OPERATION_NONCE'):
        raise UnsafeTestTarget('Conflicting operation lease')
    with locked_receipt(path) as (stream, receipt):
        if (receipt['run_id'] != target.run_id or receipt['password'] != target.password
                or receipt['state'] != 'validated' or receipt.get('operation_nonce')):
            raise UnsafeTestTarget('Browser operation requires an unused validated receipt')
        now = time.time()
        duration = min(seconds, receipt['issued_at'] + TTL_SECONDS - now)
        if duration <= 0:
            raise UnsafeTestTarget('Browser receipt expired')
        nonce = secrets.token_hex(16)
        receipt.update(state='browser-integration', operation_nonce=nonce,
                       operator=process_identity(), browser_database=approved_database,
                       browser_started_at=now, browser_expires_at=now + duration,
                       browser_monotonic_deadline=time.monotonic() + duration)
        # A failed write cannot lead to activity starting.
        write_receipt(stream, receipt)
        previous = os.environ.get('PG_TEST_OPERATION_NONCE')
        os.environ['PG_TEST_OPERATION_NONCE'] = nonce
        completed = False
        try:
            validate_browser_database()
            yield receipt['browser_monotonic_deadline']
            validate_browser_database()
            completed = True
        finally:
            try:
                stop(timeout=5)
                if is_stopped() is not True:
                    raise UnsafeTestTarget('Browser activity termination is unconfirmed')
                receipt.update(state='browser-complete' if completed else 'failed',
                               operation_nonce=None, operator=None)
                write_receipt(stream, receipt)
            except BaseException:
                # Retain lease evidence; fail closed even after shutdown errors.
                receipt.update(state='failed', operation_nonce=nonce)
                write_receipt(stream, receipt)
                raise UnsafeTestTarget('Browser shutdown failed; separate recovery review required') from None
            finally:
                if previous is None:
                    os.environ.pop('PG_TEST_OPERATION_NONCE', None)
                else:
                    os.environ['PG_TEST_OPERATION_NONCE'] = previous
