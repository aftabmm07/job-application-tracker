"""Independent committed PostgreSQL sessions; all access is opt-in and guarded."""
import os

from alembic import command
import psycopg
import pytest
from sqlalchemy import delete, inspect
from sqlalchemy.orm import Session

from app.auth_config import AuthSettings, get_auth_settings
from app.auth_limits import AuthRateLimiter, get_auth_limiter
from app.dependencies import get_db
from app.main import app
from app.models import AuthSession, EmailVerificationOutbox, EmailVerificationToken, JobApplication, User
from app.passwords import PasswordWorkLimiter
from scripts.postgres_test_harness import TestTarget, UnsafeTestTarget


@pytest.fixture(scope='session')
def pg_target(pytestconfig):
    if os.getenv('RUN_ISOLATED_POSTGRES_TESTS') != '1':
        pytest.skip('Isolated PostgreSQL integration tests require explicit approval and opt-in')
    if not pytestconfig.getoption('--isolated-safe-reports'):
        raise UnsafeTestTarget('Use the receipt-bound test runner with safe reports')
    if os.getenv('PYTEST_XDIST_WORKER'):
        raise UnsafeTestTarget('Parallel pytest workers are not supported by this single-run harness')
    return TestTarget.from_environment()


@pytest.fixture(autouse=True)
def guard_all_psycopg_connections(pg_target, monkeypatch):
    original = psycopg.connect

    def guarded(*args, **kwargs):
        # Also blocks accidental runtime engine and unrelated migration connections.
        url = pg_target.url('api').set(database=kwargs.get('dbname'))
        pg_target.validate_connect_parameters(url, args, kwargs)
        from scripts.postgres_test_provenance import verify_resources
        verify_resources(pg_target)
        connection = original(*args, **kwargs)
        try:
            pg_target.verify_connection(connection, url.database)
        except Exception:
            connection.close()
            raise
        return connection

    monkeypatch.setattr(psycopg, 'connect', guarded)
    monkeypatch.setattr(psycopg.Connection, 'connect', guarded)


@pytest.fixture
def pg_engine(pg_target):
    engine = pg_target.engine('api')
    try:
        with engine.begin() as connection:
            tables = set(inspect(connection).get_table_names())
            if not tables:
                command.upgrade(pg_target.migration_config(connection, 'api'), 'head')
            elif tables != {'alembic_version', 'users', 'sessions', 'job_applications',
                             'email_verification_tokens', 'email_verification_outbox'}:
                raise UnsafeTestTarget('Unexpected disposable API schema')
            # Every test must start empty; no adoption of existing test rows.
            from sqlalchemy import func, select
            for model in (User, AuthSession, JobApplication, EmailVerificationToken,
                          EmailVerificationOutbox):
                if connection.scalar(select(func.count()).select_from(model)):
                    raise UnsafeTestTarget('Disposable API database is not empty')
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def api(pg_engine, monkeypatch):
    settings = AuthSettings.from_environment({'SESSION_IDLE_TIMEOUT_SECONDS': '60'})
    limiter = AuthRateLimiter()
    # Exactly two worker requests; real Argon2id remains unchanged.
    monkeypatch.setattr('app.passwords._password_work_limiter', PasswordWorkLimiter(2))

    def database():
        with Session(pg_engine, autoflush=False) as db:
            yield db

    previous = app.dependency_overrides.copy()
    app.dependency_overrides.update({get_db: database, get_auth_settings: lambda: settings,
                                    get_auth_limiter: lambda: limiter})
    try:
        yield pg_engine, settings
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
        # Test-fixture teardown ONLY: real commits, only the run-specific API DB.
        with pg_engine.begin() as connection:
            for model in (EmailVerificationToken, JobApplication, AuthSession, User):
                connection.execute(delete(model))
