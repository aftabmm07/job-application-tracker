"""Selection and dependency tests with no DBAPI connections or services."""
from unittest.mock import Mock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import OperationalError

from app import database
from app.database_config import DatabaseConfigurationError
from app.database_engine import select_database_engine
from app.dependencies import get_db
from app.main import database_health


@pytest.mark.parametrize('changes', [
    {'APP_DATABASE_MODE': 'disposable'},
    {'APP_DATABASE_MODE': 'disposable', 'APPROVE_BROWSER_INTEGRATION': '1',
     'RUN_ISOLATED_POSTGRES_TESTS': '1', 'PG_TEST_RECEIPT': '/tmp/synthetic-receipt',
     'PG_TEST_OPERATION_NONCE': 'a' * 32},
    {'APP_DATABASE_MODE': 'disposable', 'APP_ENV': 'production'},
    {'APP_DATABASE_MODE': ''}, {'APP_DATABASE_NAME': 'jobtracker_db'},
    {'APP_DATABASE_NAME': 'postgres'}, {'APP_DATABASE_NAME': ''},
    {'APP_DATABASE_PORT': '55433'},
])
def test_rejection_cannot_create_engine_or_fallback(changes):
    values = {'APP_DATABASE_NAME': 'synthetic_test', 'POSTGRES_PASSWORD': 'synthetic-secret'}
    values.update(changes)
    with patch('app.database_engine.create_engine') as factory, patch('psycopg.connect') as connect:
        with pytest.raises(DatabaseConfigurationError) as error:
            select_database_engine(values)
    factory.assert_not_called()
    connect.assert_not_called()
    assert 'synthetic-secret' not in str(error.value)


def test_missing_credentials_cannot_create_engine():
    with patch('app.database_engine.create_engine') as factory:
        with pytest.raises(RuntimeError, match='POSTGRES_PASSWORD'):
            select_database_engine({'APP_DATABASE_NAME': 'synthetic_test'})
    factory.assert_not_called()


def test_ordinary_selection_is_lazy_and_unchanged():
    with patch('psycopg.connect') as connect, patch('psycopg.Connection.connect') as class_connect:
        url, engine = select_database_engine({'APP_DATABASE_NAME': 'synthetic_test',
                                             'POSTGRES_PASSWORD': 'synthetic-secret'})
        try:
            assert engine.url == url
            assert (url.host, url.port, url.username, url.database) == (
                '127.0.0.1', 5432, 'jobtracker', 'synthetic_test')
            connect.assert_not_called()
            class_connect.assert_not_called()
        finally:
            engine.dispose()


def test_dependency_and_health_use_same_selected_bind():
    # Opening/closing a Session is lazy. The health connection itself is mocked.
    dependency = get_db()
    session = next(dependency)
    try:
        assert session.get_bind() is database.get_database_engine()
        with patch.object(session.get_bind(), 'connect') as connect:
            assert database_health() == {'status': 'healthy', 'database': 'connected'}
            connect.assert_called_once_with()
            execute = connect.return_value.__enter__.return_value.execute
            assert str(execute.call_args.args[0]) == 'SELECT 1'
    finally:
        dependency.close()


def test_dependency_closes_session_on_error():
    fake = Mock()
    with patch('app.dependencies.SessionLocal', return_value=fake):
        dependency = get_db()
        assert next(dependency) is fake
        with pytest.raises(ValueError):
            dependency.throw(ValueError('synthetic'))
    fake.close.assert_called_once_with()


def test_health_connection_failure_stays_sanitized():
    with patch.object(database.get_database_engine(), 'connect',
                      side_effect=OperationalError('secret', {}, Exception('private-secret'))):
        with pytest.raises(HTTPException) as error:
            database_health()
    assert error.value.status_code == 503
    assert error.value.detail == 'Database unavailable'
