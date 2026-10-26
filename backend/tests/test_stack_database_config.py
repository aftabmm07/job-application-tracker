"""Offline guards for a future, explicitly selected container-stack database."""
import os
from pathlib import Path
import runpy
from unittest.mock import MagicMock, Mock, patch

import pytest
from sqlalchemy.engine import URL

from app.database_config import (
    DatabaseConfigurationError, runtime_database_mode, runtime_database_name,
    stack_database_target,
)
from app.database_engine import select_database_engine
from app.migration_safety import check_stack_target, migration_url


RUN_ID = "a1b2c3d4e5f60718"
NAME = f"jobtracker_stack_{RUN_ID}"
PASSWORD = "synthetic-stack-only-private-password"
DATABASE_MODULE = Path(__file__).resolve().parents[1] / "app/database.py"


def stack_values():
    return {
        "APP_ENV": "development", "APP_DATABASE_MODE": "stack", "STACK_RUN_ID": RUN_ID,
        "STACK_DATABASE_NAME": NAME, "STACK_DATABASE_HOST": "db",
        "STACK_DATABASE_PORT": "5432", "STACK_DATABASE_USER": "jobtracker_stack",
        "STACK_POSTGRES_PASSWORD": PASSWORD,
    }


def test_stack_runtime_and_migration_use_the_same_explicit_target():
    values = stack_values()
    target = stack_database_target(values)
    assert (target.name, target.project, target.host, target.port, target.user) == (
        NAME, f"jobtracker-stack-{RUN_ID}", "db", 5432, "jobtracker_stack")
    assert PASSWORD not in repr(target)
    assert runtime_database_name(values) == NAME
    with patch("app.database_engine.create_engine") as factory, \
            patch("app.database_engine.event.listen") as listen, patch("psycopg.connect") as connect:
        url, engine = select_database_engine(values)
    factory.assert_called_once_with(url, pool_pre_ping=True, hide_parameters=True)
    listen.assert_called_once_with(engine, "connect", listen.call_args.args[2])
    connect.assert_not_called()
    assert (url.drivername, url.host, url.port, url.username, url.database) == (
        "postgresql+psycopg", "db", 5432, "jobtracker_stack", NAME)
    assert url.password == PASSWORD
    assert PASSWORD not in str(url)
    with patch.dict(os.environ, values | {"MIGRATION_DATABASE_NAME": NAME}, clear=True), \
            patch("app.migration_safety.load_dotenv") as dotenv, patch("psycopg.connect") as connect:
        migration = migration_url()
    dotenv.assert_not_called()
    connect.assert_not_called()
    assert migration == url


def test_stack_connect_listener_checks_server_identity_without_exposing_credentials():
    with patch("app.database_engine.create_engine"), patch("app.database_engine.event.listen") as listen:
        select_database_engine(stack_values())
    verify = listen.call_args.args[2]
    dbapi = MagicMock()
    cursor = dbapi.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = (NAME, "jobtracker_stack", "public")
    verify(dbapi, None)
    assert "current_database()" in cursor.execute.call_args.args[0]
    dbapi.rollback.assert_called_once_with()
    for identity in [("jobtracker_db", "jobtracker_stack", "public"),
                     (NAME, "jobtracker", "public"), (NAME, "jobtracker_stack", "other")]:
        cursor.fetchone.return_value = identity
        with pytest.raises(DatabaseConfigurationError) as error:
            verify(dbapi, None)
        assert PASSWORD not in str(error.value)
    cursor.execute.side_effect = RuntimeError(PASSWORD)
    with pytest.raises(DatabaseConfigurationError) as error:
        verify(dbapi, None)
    assert PASSWORD not in str(error.value)


@pytest.mark.parametrize("change", [
    {"APP_ENV": "production"}, {"APP_ENV": ""}, {"APP_DATABASE_MODE": "ordinary"},
    {"STACK_RUN_ID": ""}, {"STACK_RUN_ID": "A" * 16},
    {"STACK_RUN_ID": "a" * 15}, {"STACK_RUN_ID": "a" * 17},
    {"STACK_DATABASE_NAME": "jobtracker_db"}, {"STACK_DATABASE_NAME": "postgres"},
    {"STACK_DATABASE_NAME": "jobtracker_demo_a1b2c3d4e5f60718"},
    {"STACK_DATABASE_HOST": "localhost"}, {"STACK_DATABASE_HOST": "127.0.0.1"},
    {"STACK_DATABASE_HOST": "host.docker.internal"}, {"STACK_DATABASE_HOST": "db.example.com"},
    {"STACK_DATABASE_PORT": "55434"}, {"STACK_DATABASE_PORT": ""},
    {"STACK_DATABASE_USER": "jobtracker"}, {"STACK_DATABASE_USER": "jobtracker_demo"},
    {"STACK_POSTGRES_PASSWORD": "short"}, {"STACK_POSTGRES_PASSWORD": " " * 20},
    {"APP_DATABASE_NAME": NAME}, {"POSTGRES_PASSWORD": "other-password"},
    {"DEMO_RUN_ID": RUN_ID}, {"DEMO_POSTGRES_PASSWORD": "other-password"},
    {"MIGRATION_DATABASE_HOST": "db"}, {"MIGRATION_DATABASE_PORT": "5432"},
    {"DATABASE_URL": "postgresql://unsafe"}, {"PGHOST": "db"},
])
def test_stack_rejection_precedes_engine_or_connection(change):
    values = stack_values() | change
    with patch("app.database_engine.create_engine") as factory, patch("psycopg.connect") as connect:
        with pytest.raises(DatabaseConfigurationError) as error:
            select_database_engine(values)
    factory.assert_not_called()
    connect.assert_not_called()
    assert PASSWORD not in str(error.value) + repr(error.value)


@pytest.mark.parametrize("missing", [
    "STACK_RUN_ID", "STACK_DATABASE_NAME", "STACK_DATABASE_HOST", "STACK_DATABASE_PORT",
    "STACK_DATABASE_USER", "STACK_POSTGRES_PASSWORD", "APP_ENV",
])
def test_missing_stack_setting_fails_closed(missing):
    values = stack_values()
    del values[missing]
    with patch("app.database_engine.create_engine") as factory:
        with pytest.raises(DatabaseConfigurationError):
            select_database_engine(values)
    factory.assert_not_called()


@pytest.mark.parametrize("mode", [None, "ordinary", "demo", "disposable"])
def test_stack_settings_cannot_fall_back_to_another_mode(mode):
    values = stack_values()
    if mode is None:
        del values["APP_DATABASE_MODE"]
    else:
        values["APP_DATABASE_MODE"] = mode
    with pytest.raises(DatabaseConfigurationError):
        runtime_database_mode(values)


@pytest.mark.parametrize("migration_name", [None, "jobtracker_db", "postgres", "wrong_stack"])
def test_stack_migration_requires_exact_explicit_name(migration_name):
    values = stack_values()
    if migration_name is not None:
        values["MIGRATION_DATABASE_NAME"] = migration_name
    with patch.dict(os.environ, values, clear=True), patch("app.migration_safety.load_dotenv") as dotenv:
        with pytest.raises(RuntimeError) as error:
            migration_url()
    dotenv.assert_not_called()
    assert PASSWORD not in str(error.value)


def stack_connection(identity=(NAME, "public", "jobtracker_stack", 5432), revision=None,
                     tables=()):
    connection = Mock()
    connection.dialect.name = "postgresql"
    connection.engine.url = URL.create("postgresql+psycopg", username="jobtracker_stack",
                                       password=PASSWORD, host="db", port=5432, database=NAME)
    connection.scalar.side_effect = identity
    inspector = Mock()
    inspector.get_table_names.return_value = list(tables)
    inspector.get_view_names.return_value = []
    inspector.get_materialized_view_names.return_value = []
    connection.scalars.return_value.all.return_value = [revision] if revision else []
    return connection, inspector


@pytest.mark.parametrize("revision,tables", [
    (None, ()),
    ("0001_legacy", ("alembic_version", "job_applications")),
    ("0005_user_profile", ("alembic_version", "job_applications", "users", "sessions",
                           "email_verification_tokens", "email_verification_outbox")),
])
def test_stack_migration_accepts_only_expected_identity_and_schema(revision, tables):
    connection, inspector = stack_connection(revision=revision, tables=tables)
    with patch("app.migration_safety.inspect", return_value=inspector):
        check_stack_target(connection, stack_database_target(stack_values()))


@pytest.mark.parametrize("identity,revision,tables", [
    (("jobtracker_db", "public", "jobtracker_stack", 5432), None, ()),
    ((NAME, "other", "jobtracker_stack", 5432), None, ()),
    ((NAME, "public", "jobtracker", 5432), None, ()),
    ((NAME, "public", "jobtracker_stack", 55434), None, ()),
    ((NAME, "public", "jobtracker_stack", 5432), None, ("users",)),
    ((NAME, "public", "jobtracker_stack", 5432), "unknown_revision", ("alembic_version",)),
    ((NAME, "public", "jobtracker_stack", 5432), "0005_user_profile", ("alembic_version",)),
])
def test_stack_migration_rejects_wrong_identity_revision_or_tables(identity, revision, tables):
    connection, inspector = stack_connection(identity=identity, revision=revision, tables=tables)
    with patch("app.migration_safety.inspect", return_value=inspector):
        with pytest.raises(RuntimeError) as error:
            check_stack_target(connection, stack_database_target(stack_values()))
    assert PASSWORD not in str(error.value)


@pytest.mark.parametrize("change", [
    {"host": "127.0.0.1"}, {"password": "different-synthetic-password"},
    {"query": {"options": "-c search_path=other"}},
])
def test_stack_migration_rejects_supplied_connection_mismatch_before_sql(change):
    connection, _ = stack_connection()
    connection.engine.url = connection.engine.url.set(**change)
    with pytest.raises(RuntimeError):
        check_stack_target(connection, stack_database_target(stack_values()))
    connection.scalar.assert_not_called()


def test_stack_runtime_and_auth_settings_never_load_ordinary_dotenv():
    with patch.dict(os.environ, stack_values(), clear=True), \
            patch("dotenv.load_dotenv") as dotenv, \
            patch("app.database_engine.create_engine"), \
            patch("app.database_engine.event.listen"), patch("psycopg.connect") as connect:
        runpy.run_path(str(DATABASE_MODULE))
    dotenv.assert_not_called()
    connect.assert_not_called()
    from app.auth_config import get_auth_settings
    get_auth_settings.cache_clear()
    try:
        with patch.dict(os.environ, stack_values(), clear=True), \
                patch("app.auth_config.load_dotenv") as auth_dotenv:
            assert get_auth_settings().environment == "development"
        auth_dotenv.assert_not_called()
    finally:
        get_auth_settings.cache_clear()
