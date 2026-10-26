"""Offline safety checks: fake DBAPI/Docker only; never contact either service."""
from unittest.mock import MagicMock

import pytest

from scripts.postgres_test_harness import CASES, TestTarget, UnsafeTestTarget

RUN = '1a' * 16
SECRET = '2b' * 32


@pytest.fixture
def target(monkeypatch):
    monkeypatch.setenv('RUN_ISOLATED_POSTGRES_TESTS', '1')
    monkeypatch.setenv('PG_TEST_RUN_ID', RUN)
    monkeypatch.setenv('PG_TEST_PASSWORD', SECRET)
    monkeypatch.setattr('scripts.postgres_test_provenance.authorize_receipt', lambda target: {})
    return TestTarget.from_environment()


def parameters(target):
    return dict(host='127.0.0.1', port=55433, user=target.role, password=SECRET,
                dbname=target.database('api'), connect_timeout=3,
                application_name='jobtracker-isolated-tests',
                options='-c statement_timeout=5000 -c lock_timeout=2000 -c idle_in_transaction_session_timeout=15000')


@pytest.mark.parametrize('changes', [{}, {'RUN_ISOLATED_POSTGRES_TESTS': '0'},
    {'RUN_ISOLATED_POSTGRES_TESTS': '1'},
    {'RUN_ISOLATED_POSTGRES_TESTS': '1', 'PG_TEST_RUN_ID': RUN, 'PG_TEST_PASSWORD': 'short'}])
def test_opt_in_and_credentials_required(changes):
    with pytest.raises(UnsafeTestTarget) as error:
        TestTarget.from_environment(changes)
    assert SECRET not in str(error.value)


@pytest.mark.parametrize('field,value', [('drivername', 'sqlite'), ('drivername', 'postgresql'),
    ('drivername', 'postgresql+psycopg2'), ('host', 'localhost'), ('host', '::1'),
    ('port', 5432), ('port', 55432), ('database', 'jobtracker_db'),
    ('database', 'jobtracker_restore_test'), ('database', 'jobtracker_auth_ci'),
    ('username', 'jobtracker'), ('password', 'project-password'), ('query', {'service': 'other'})])
def test_target_rejected_before_dbapi(target, field, value, monkeypatch):
    connect = MagicMock(side_effect=AssertionError('DBAPI must not be called'))
    monkeypatch.setattr('psycopg.connect', connect)
    engine = target.engine('api')
    engine.url = engine.url.set(**{field: value})
    try:
        with pytest.raises(UnsafeTestTarget):
            engine.connect()
        connect.assert_not_called()
    finally:
        engine.dispose()


@pytest.mark.parametrize('extra', [{'hostaddr': '127.0.0.1'}, {'service': 'other'},
    {'options': '-c jobtracker.test_run=forged'}, {'password': 'wrong'}, {'port': 5432}, {'context': 'invalid'}])
def test_libpq_parameter_overrides_blocked(target, extra):
    params = parameters(target) | extra
    with pytest.raises(UnsafeTestTarget):
        target.validate_connect_parameters(target.url('api'), [], params)
    with pytest.raises(UnsafeTestTarget):
        target.validate_connect_parameters(target.url('api'), ['postgresql://secret'], parameters(target))


def test_approved_target_parameters_and_repr(target):
    for case in CASES:
        target.validate_url(target.url(case))
    target.validate_connect_parameters(target.url('api'), [], parameters(target))
    assert SECRET not in repr(target)


def fake_connection(row):
    connection = MagicMock()
    connection.cursor.return_value.__enter__.return_value.fetchone.return_value = row
    return connection


@pytest.mark.parametrize('index,value', [(0, 160010), (0, 180000), (1, None), (1, 'other-run'),
    (2, 'jobtracker_db'), (3, 'jobtracker'), (4, 'private'), (5, '/unexpected')])
def test_wrong_server_marker_version_or_identity_rejected(target, index, value):
    row = [170010, RUN, target.database('api'), target.role, 'public', '/var/lib/postgresql/data']
    row[index] = value
    connection = fake_connection(row)
    with pytest.raises(UnsafeTestTarget):
        target.verify_connection(connection, target.database('api'))
    assert connection.cursor.return_value.__enter__.return_value.execute.call_count == 1


def test_verified_identity_is_read_only(target):
    connection = fake_connection([170010, RUN, target.database('api'), target.role, 'public', '/var/lib/postgresql/data'])
    target.verify_connection(connection, target.database('api'))
    connection.rollback.assert_called_once()
    sql = connection.cursor.return_value.__enter__.return_value.execute.call_args.args[0]
    assert sql.startswith('SELECT ')


@pytest.mark.parametrize('name', ['PGHOSTADDR', 'PGSERVICE', 'PGOPTIONS', 'PGPASSWORD'])
def test_environment_cannot_redirect_libpq(target, monkeypatch, name):
    monkeypatch.setenv(name, 'do-not-disclose')
    with pytest.raises(UnsafeTestTarget) as error:
        target.engine('api')
    assert 'do-not-disclose' not in str(error.value)


def test_revoking_opt_in_blocks_new_connections(target, monkeypatch):
    engine = target.engine('api')
    connect = MagicMock(side_effect=AssertionError('DBAPI must not run'))
    monkeypatch.setattr('psycopg.connect', connect)
    monkeypatch.setenv('RUN_ISOLATED_POSTGRES_TESTS', '0')
    try:
        with pytest.raises(UnsafeTestTarget):
            engine.connect()
        connect.assert_not_called()
    finally:
        engine.dispose()


def test_dialect_parameters_match_exact_guard(target):
    engine = target.engine('api')
    try:
        args, params = engine.dialect.create_connect_args(engine.url)
        expected = parameters(target)
        for key in ('connect_timeout', 'application_name', 'options'):
            params[key] = expected[key]
        target.validate_connect_parameters(engine.url, args, params)
    finally:
        engine.dispose()



def test_execution_without_creation_receipt_is_forbidden(monkeypatch):
    monkeypatch.setenv('RUN_ISOLATED_POSTGRES_TESTS', '1')
    monkeypatch.setenv('PG_TEST_RUN_ID', RUN)
    monkeypatch.setenv('PG_TEST_PASSWORD', SECRET)
    monkeypatch.delenv('PG_TEST_RECEIPT', raising=False)
    docker = MagicMock(side_effect=AssertionError('No Docker operation allowed'))
    monkeypatch.setattr('scripts.postgres_test_provenance.docker', docker)
    with pytest.raises(UnsafeTestTarget):
        TestTarget.from_environment().engine('api')
    docker.assert_not_called()
