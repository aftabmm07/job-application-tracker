"""Runtime configuration checks never connect to PostgreSQL."""
import os
from pathlib import Path
import runpy
import subprocess
import sys
from unittest.mock import patch

import pytest

from app.database import DatabaseConfigurationError, runtime_database_name


DATABASE_MODULE = Path(__file__).resolve().parents[1] / "app" / "database.py"


@pytest.mark.parametrize("environment", ["development", "production"])
@pytest.mark.parametrize("name", [
    None, "", " ", "jobtracker_db", "postgres", "template0", "template1",
    "JOBTRACKER_DB", " jobtracker_db", "jobtracker_db ", "test db", "test-db",
    "test/db", "test?password=private-marker", "test\n", "test:db",
    "éxample", "1test", "_test", "a" * 64,
])
def test_invalid_runtime_target_fails_before_engine_creation(name, environment, caplog):
    values = {"APP_ENV": environment, "POSTGRES_PASSWORD": "private-password-marker"}
    if name is not None:
        values["APP_DATABASE_NAME"] = name
    # Disable .env loading to exercise missing values independently of local files.
    with patch.dict(os.environ, values, clear=True), patch("dotenv.load_dotenv"), \
            patch("sqlalchemy.create_engine") as create_engine:
        with pytest.raises(ValueError) as error:
            runpy.run_path(str(DATABASE_MODULE))
    create_engine.assert_not_called()
    assert type(error.value).__name__ == "DatabaseConfigurationError"
    assert "APP_DATABASE_NAME" in str(error.value)
    assert "private-password-marker" not in str(error.value) + caplog.text
    assert "private-marker" not in str(error.value) + caplog.text
    assert "postgresql://" not in str(error.value)
    assert "postgresql+psycopg://" not in str(error.value)


@pytest.mark.parametrize("environment", ["development", "production"])
@pytest.mark.parametrize("name", [
    "jobtracker_auth_fresh_9b2", "jobtracker_auth_legacy_9b2", "jobtracker_auth_ci",
    "jobtracker_production", "a", "a" * 63,
])
def test_explicit_target_is_used_without_connecting(name, environment):
    values = {"APP_ENV": environment, "APP_DATABASE_NAME": name,
              "POSTGRES_PASSWORD": "private-password-marker"}
    with patch.dict(os.environ, values, clear=True), patch("dotenv.load_dotenv"), \
            patch("sqlalchemy.create_engine") as create_engine:
        module = runpy.run_path(str(DATABASE_MODULE))
    create_engine.assert_called_once()
    assert create_engine.call_args.args[0].database == name
    assert module["DATABASE_URL"].database == name
    create_engine.return_value.connect.assert_not_called()


@pytest.mark.parametrize("value", [123, "test\x00db"])
def test_invalid_values_not_representable_in_environment_are_rejected(value):
    with pytest.raises(DatabaseConfigurationError):
        runtime_database_name({"APP_DATABASE_NAME": value})


@pytest.mark.parametrize("name", [None, "jobtracker_db", "invalid/name"])
def test_normal_application_import_rejects_target_before_engine_creation(name):
    environment = os.environ.copy()
    environment.update(APP_ENV="development", POSTGRES_PASSWORD="private-password-marker")
    environment.pop("APP_DATABASE_NAME", None)
    if name is not None:
        environment["APP_DATABASE_NAME"] = name
    # A fresh process proves the normal startup import cannot use a cached module.
    code = """
from unittest.mock import patch
with patch('dotenv.load_dotenv'), patch('sqlalchemy.create_engine') as engine:
    try:
        import app.main
    except ValueError as error:
        assert type(error).__name__ == 'DatabaseConfigurationError'
        assert 'APP_DATABASE_NAME' in str(error)
    else:
        raise AssertionError('Startup accepted an unsafe target')
    engine.assert_not_called()
"""
    result = subprocess.run([sys.executable, "-c", code], env=environment,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert "private-password-marker" not in result.stdout + result.stderr
