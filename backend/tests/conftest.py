"""Match PostgreSQL foreign-key enforcement in every SQLite-backed test."""
import os
import sqlite3
import subprocess
from pathlib import Path

import psycopg
import pytest

from sqlalchemy import event
from sqlalchemy.engine import Engine

pytest_plugins = ['scripts.postgres_test_reporting']

# Test collection imports app.main. This names its otherwise unused runtime
# engine; API fixtures still override get_db with isolated SQLite connections.
os.environ.setdefault("APP_DATABASE_NAME", "jobtracker_auth_ci")


@event.listens_for(Engine, "connect")
def enable_sqlite_foreign_keys(dbapi_connection, connection_record):
    if isinstance(dbapi_connection, sqlite3.Connection):
        previous = dbapi_connection.autocommit
        dbapi_connection.autocommit = True
        try:
            dbapi_connection.execute("PRAGMA foreign_keys=ON")
        finally:
            dbapi_connection.autocommit = previous


# Offline tests must never fall through to the runtime PostgreSQL engine.
# The new opt-in suite installs its stricter target/parameter/identity guards.
@pytest.fixture(autouse=True)
def prohibit_unapproved_postgres(request, monkeypatch):
    if request.node.path.parent.name == 'postgresql':
        return
    if (request.node.path.name == 'test_postgres_ownership.py'
            and os.getenv('RUN_POSTGRES_OWNERSHIP_TESTS') == '1'):
        return  # Existing separately approved CI service test.

    def forbidden(*args, **kwargs):
        raise RuntimeError('PostgreSQL connections are forbidden in offline tests')

    monkeypatch.setattr(psycopg, 'connect', forbidden)
    monkeypatch.setattr(psycopg.Connection, 'connect', forbidden)


@pytest.fixture(autouse=True)
def prohibit_offline_docker_commands(request, monkeypatch):
    if request.node.path.parent.name == 'postgresql':
        return  # Receipt-bound integration tests verify each Docker operation.
    original = subprocess.run

    def offline_run(command, *args, **kwargs):
        if isinstance(command, (list, tuple)) and command and Path(str(command[0])).name in {'docker', 'docker.exe'}:
            raise RuntimeError('Docker commands are forbidden in offline tests')
        return original(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, 'run', offline_run)
