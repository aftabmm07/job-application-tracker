"""Configuration checks with mocked connections, never PostgreSQL access."""
import os
from pathlib import Path
import runpy
from unittest.mock import Mock, patch

import pytest
from app.database_config import DatabaseConfigurationError, runtime_database_mode, runtime_database_name, validate_database_name
from app.migration_safety import check_target, migration_url

DATABASE_MODULE = Path(__file__).resolve().parents[1] / 'app' / 'database.py'
INVALID_NAMES = [None, '', ' ', 'jobtracker_db', 'postgres', 'template0', 'template1',
                 'UPPER', 'test-db', 'test/db', 'a\n', 'é', '1test', 'a' * 64,
                 'private-secret?password=marker']


@pytest.mark.parametrize('name', INVALID_NAMES)
def test_shared_identifier_rejection(name):
    with pytest.raises(DatabaseConfigurationError) as error:
        validate_database_name(name, 'DATABASE_NAME')
    assert 'private-secret' not in str(error.value)


@pytest.mark.parametrize('name', ['a', 'a' * 63, 'synthetic_test_123'])
def test_valid_identifier_is_not_approval(name):
    assert validate_database_name(name, 'DATABASE_NAME') == name


@pytest.mark.parametrize('mode', [None, '', ' ', 'Ordinary', 'disposable ', 'unknown', 1])
def test_invalid_mode(mode):
    with pytest.raises(DatabaseConfigurationError):
        runtime_database_mode({'APP_DATABASE_MODE': mode})


def test_modes_parse_but_disposable_cannot_authorize_connection():
    assert runtime_database_mode({}) == 'ordinary'
    assert runtime_database_mode({'APP_DATABASE_MODE': 'ordinary'}) == 'ordinary'
    assert runtime_database_mode({'APP_DATABASE_MODE': 'disposable'}) == 'disposable'
    with pytest.raises(DatabaseConfigurationError, match='unavailable'):
        runtime_database_name({'APP_DATABASE_MODE': 'disposable'})


@pytest.mark.parametrize('environment', ['production', ' Production '])
def test_production_disposable_refused(environment):
    with pytest.raises(DatabaseConfigurationError, match='forbidden in production'):
        runtime_database_mode({'APP_DATABASE_MODE': 'disposable', 'APP_ENV': environment})


@pytest.mark.parametrize('environment', [None, '', 'staging', 1])
def test_invalid_environment(environment):
    with pytest.raises(DatabaseConfigurationError):
        runtime_database_mode({'APP_ENV': environment})


@pytest.mark.parametrize('changes', [
    {'APP_DATABASE_MODE': ''}, {'APP_DATABASE_MODE': 'invalid'},
    {'APP_DATABASE_MODE': 'disposable'},
    {'APP_DATABASE_MODE': 'disposable', 'APP_ENV': 'production'},
    {'APP_DATABASE_HOST': 'other'}, {'APP_DATABASE_PORT': '55433'},
    {'APP_DATABASE_USER': 'other'}, {'APP_ENV': 'invalid'},
])
def test_runtime_rejection_precedes_engine_and_connection(changes):
    values = {'APP_DATABASE_NAME': 'synthetic_test', 'POSTGRES_PASSWORD': 'synthetic-secret'}
    values.update(changes)
    with patch.dict(os.environ, values, clear=True), patch('dotenv.load_dotenv'), \
            patch('app.database_engine.create_engine') as engine, patch('psycopg.connect') as connect:
        with pytest.raises(DatabaseConfigurationError) as error:
            runpy.run_path(str(DATABASE_MODULE))
    engine.assert_not_called()
    connect.assert_not_called()
    assert 'synthetic-secret' not in str(error.value)


def test_disposable_without_credentials_cannot_create_engine():
    with patch.dict(os.environ, {'APP_DATABASE_MODE': 'disposable'}, clear=True), \
            patch('dotenv.load_dotenv'), patch('app.database_engine.create_engine') as engine:
        with pytest.raises(DatabaseConfigurationError, match='unavailable'):
            runpy.run_path(str(DATABASE_MODULE))
    engine.assert_not_called()


@pytest.mark.parametrize('key', ['APP_DATABASE_NAME', 'POSTGRES_PASSWORD'])
def test_disposable_conflict(key):
    with pytest.raises(DatabaseConfigurationError, match='conflicts'):
        runtime_database_name({'APP_DATABASE_MODE': 'disposable', key: 'private-marker'})


@pytest.mark.parametrize('name', INVALID_NAMES)
def test_migration_configuration_rejection(name):
    values = {'POSTGRES_PASSWORD': 'synthetic-secret'}
    if name is not None:
        values['MIGRATION_DATABASE_NAME'] = name
    with patch.dict(os.environ, values, clear=True), patch('dotenv.load_dotenv'), \
            patch('app.database_engine.create_engine') as engine, patch('psycopg.connect') as connect:
        with pytest.raises(RuntimeError) as error:
            migration_url()
    engine.assert_not_called()
    connect.assert_not_called()
    assert 'synthetic-secret' not in str(error.value)
    assert 'private-secret' not in str(error.value)


@pytest.mark.parametrize('name', INVALID_NAMES)
def test_supplied_invalid_expectation_rejected_before_sql(name):
    connection = Mock()
    connection.dialect.name = 'postgresql'
    with pytest.raises(RuntimeError):
        check_target(connection, name)
    connection.scalar.assert_not_called()


@pytest.mark.parametrize('actual', INVALID_NAMES + ['different_test'])
def test_supplied_invalid_or_mismatched_identity(actual):
    connection = Mock()
    connection.dialect.name = 'postgresql'
    connection.scalar.return_value = actual
    with pytest.raises(RuntimeError):
        check_target(connection, 'synthetic_test')
    assert connection.scalar.call_count == 1


def test_valid_identity_and_sqlite():
    connection = Mock()
    connection.dialect.name = 'postgresql'
    connection.scalar.side_effect = ['synthetic_test', 'public']
    check_target(connection, 'synthetic_test')
    assert connection.scalar.call_count == 2
    connection.reset_mock()
    connection.dialect.name = 'sqlite'
    check_target(connection)
    connection.scalar.assert_not_called()
