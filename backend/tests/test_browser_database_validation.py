"""Synthetic private receipts only; all external operations forbidden."""
from datetime import UTC, datetime
import json
import os
import time
from unittest.mock import Mock

import pytest
import psycopg
import sqlalchemy

from scripts import postgres_test_provenance as provenance
from scripts.browser_database_validation import validate_browser_database
from scripts.postgres_test_harness import TestTarget, UnsafeTestTarget


@pytest.fixture
def browser(tmp_path, monkeypatch):
    for key in list(os.environ):
        if key.startswith(('PG', 'APP_DATABASE_', 'MIGRATION_DATABASE_')) or key in {'POSTGRES_PASSWORD', 'DATABASE_URL'}:
            monkeypatch.delenv(key)
    monkeypatch.setattr(provenance, 'ROOT', tmp_path)
    target = TestTarget('ab' * 16, 'cd' * 32)
    receipt = dict(version=1, run_id=target.run_id, password=target.password,
                   issued_at=time.time(), receipt_nonce='ef' * 16,
                   approved=provenance.approved_configuration(target),
                   state='browser-integration', operation_nonce='12' * 16,
                   browser_database=target.database('api'), browser_started_at=time.time(),
                   browser_expires_at=time.time() + 300, browser_monotonic_deadline=time.monotonic() + 300,
                   operator=provenance.process_identity(), container_id='a' * 64,
                   network_id='b' * 64, image_id='sha256:' + 'c' * 64,
                   container_created=datetime.now(UTC).isoformat())
    path, stream = provenance.new_receipt(target)
    with stream:
        provenance.write_receipt(stream, receipt)
    for key, value in dict(APP_ENV='development', APP_DATABASE_MODE='disposable',
                           APPROVE_BROWSER_INTEGRATION='1', RUN_ISOLATED_POSTGRES_TESTS='1',
                           PG_TEST_RECEIPT=str(path), PG_TEST_OPERATION_NONCE=receipt['operation_nonce'],
                           BROWSER_DATABASE_NAME=target.database('api')).items():
        monkeypatch.setenv(key, value)
    guards = []
    for module, name in [(provenance, 'docker'), (provenance, 'verify_resources'),
                         (sqlalchemy, 'create_engine'), (psycopg, 'connect')]:
        guard = Mock(side_effect=AssertionError('External operation forbidden'))
        monkeypatch.setattr(module, name, guard)
        guards.append(guard)
    yield target, receipt, path
    for guard in guards:
        guard.assert_not_called()


def test_valid_offline_preflight_does_not_enable_runtime_or_harness(browser):
    from app.database_config import DatabaseConfigurationError, runtime_database_name
    target, _, _ = browser
    url = validate_browser_database()
    assert url.host == '127.0.0.1' and url.port == 55433
    assert url.username == target.role and url.database == target.database('api')
    assert url.password == target.password
    assert target.password not in repr(url)
    with pytest.raises(DatabaseConfigurationError):
        runtime_database_name(os.environ)
    with pytest.raises(UnsafeTestTarget):
        target.authorize()  # Browser leases cannot authorize existing operations.


@pytest.mark.parametrize('key,value', [
    ('APP_DATABASE_MODE', 'ordinary'), ('APP_DATABASE_MODE', ''), ('APP_ENV', 'production'),
    ('APPROVE_BROWSER_INTEGRATION', '0'), ('RUN_ISOLATED_POSTGRES_TESTS', '0'),
    ('PG_TEST_RECEIPT', ''), ('PG_TEST_OPERATION_NONCE', ''), ('PG_TEST_OPERATION_NONCE', 'wrong'),
    ('BROWSER_DATABASE_NAME', 'jobtracker_db'), ('BROWSER_DATABASE_NAME', 'postgres'),
    ('BROWSER_DATABASE_NAME', 'template0'), ('BROWSER_DATABASE_NAME', 'template1'),
    ('BROWSER_DATABASE_NAME', ''), ('BROWSER_DATABASE_NAME', 'unrelated'),
    ('POSTGRES_PASSWORD', 'synthetic-secret'), ('DATABASE_URL', 'synthetic-secret'),
    ('APP_DATABASE_NAME', 'synthetic'), ('APP_DATABASE_HOST', '127.0.0.1'),
    ('APP_DATABASE_PORT', '55433'), ('APP_DATABASE_USER', 'synthetic'),
    ('MIGRATION_DATABASE_NAME', 'synthetic'), ('PGHOST', '127.0.0.1'),
    ('PG_TEST_PASSWORD', 'synthetic-secret'), ('PG_TEST_RUN_ID', 'ab' * 16),
])
def test_configuration_rejection(browser, monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    with pytest.raises(UnsafeTestTarget, match='preflight refused') as error:
        validate_browser_database()
    assert 'synthetic-secret' not in str(error.value)


@pytest.mark.parametrize('kind', ['expired', 'future', 'permissions', 'malformed', 'run',
    'password', 'operator', 'nonce', 'state', 'host', 'port', 'role', 'database', 'container'])
def test_receipt_rejection(browser, kind):
    _, receipt, path = browser
    if kind == 'permissions':
        path.chmod(0o644)
    elif kind == 'malformed':
        path.write_text('{')
    else:
        if kind == 'expired': receipt['issued_at'] -= provenance.TTL_SECONDS + 1
        elif kind == 'future': receipt['issued_at'] += 60
        elif kind == 'run': receipt['run_id'] = 'fe' * 16
        elif kind == 'password': receipt['password'] = 'malformed'
        elif kind == 'operator': receipt['operator']['start_ticks'] = 'wrong'
        elif kind == 'nonce': receipt['operation_nonce'] = 'wrong'
        elif kind == 'state': receipt['state'] = 'testing'
        elif kind == 'host': receipt['approved']['host'] = 'localhost'
        elif kind == 'port': receipt['approved']['port'] = 5432
        elif kind == 'role': receipt['approved']['role'] = 'jobtracker'
        elif kind == 'database': receipt['approved']['databases'] = ['jobtracker_db']
        elif kind == 'container': receipt['container_id'] = 'invalid'
        path.write_text(json.dumps(receipt))
    with pytest.raises(UnsafeTestTarget, match='preflight refused'):
        validate_browser_database()


def test_migration_database_not_approved_for_browser(browser, monkeypatch):
    target, _, _ = browser
    monkeypatch.setenv('BROWSER_DATABASE_NAME', target.database('fresh'))
    with pytest.raises(UnsafeTestTarget):
        validate_browser_database()


@pytest.mark.parametrize('nonce', ['short', 'g' * 32, 'a' * 64])
def test_matching_but_malformed_nonce_rejected(browser, monkeypatch, nonce):
    _, receipt, path = browser
    receipt['operation_nonce'] = nonce
    path.write_text(json.dumps(receipt))
    monkeypatch.setenv('PG_TEST_OPERATION_NONCE', nonce)
    with pytest.raises(UnsafeTestTarget):
        validate_browser_database()
