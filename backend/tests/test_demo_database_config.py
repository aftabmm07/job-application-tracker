"""Offline guards for the separate, opt-in PostgreSQL demo target."""
import os
from pathlib import Path
import runpy
from unittest.mock import patch

import pytest
import yaml

from app.database_config import DatabaseConfigurationError, demo_database_target, runtime_database_name
from app.database_engine import select_database_engine
from app.migration_safety import migration_url


RUN_ID = "a1b2c3d4e5f60718"
PASSWORD = "synthetic-demo-only-private-password"
NAME = f"jobtracker_demo_{RUN_ID}"
ROOT = Path(__file__).resolve().parents[2]


def demo_values():
    return {"APP_ENV": "development", "APP_DATABASE_MODE": "demo",
            "DEMO_RUN_ID": RUN_ID, "DEMO_POSTGRES_PASSWORD": PASSWORD}


def test_demo_runtime_and_migrations_select_only_same_fixed_target():
    values = demo_values()
    target = demo_database_target(values)
    assert (target.name, target.project) == (NAME, f"jobtracker-demo-{RUN_ID}")
    assert PASSWORD not in repr(target)
    assert runtime_database_name(values) == NAME
    with patch("app.database_engine.create_engine") as factory, patch("psycopg.connect") as connect:
        url, _ = select_database_engine(values)
    factory.assert_called_once_with(url, pool_pre_ping=True)
    connect.assert_not_called()
    assert (url.host, url.port, url.username, url.database) == (
        "127.0.0.1", 55434, "jobtracker_demo", NAME)
    assert url.password == PASSWORD
    assert PASSWORD not in str(url)
    with patch.dict(os.environ, values | {"MIGRATION_DATABASE_NAME": NAME}, clear=True), \
            patch("dotenv.load_dotenv") as dotenv, patch("psycopg.connect") as connect:
        migration = migration_url()
    dotenv.assert_not_called()
    connect.assert_not_called()
    assert migration == url


def test_demo_requires_explicit_development_environment():
    values = demo_values()
    del values["APP_ENV"]
    with pytest.raises(DatabaseConfigurationError, match="APP_ENV=development"):
        demo_database_target(values)


@pytest.mark.parametrize("change", [
    {"APP_ENV": "production"}, {"APP_ENV": ""}, {"APP_DATABASE_MODE": "ordinary"},
    {"DEMO_RUN_ID": ""}, {"DEMO_RUN_ID": "A" * 16}, {"DEMO_RUN_ID": "a" * 15},
    {"DEMO_RUN_ID": "a" * 17}, {"DEMO_POSTGRES_PASSWORD": "short"},
    {"DEMO_POSTGRES_PASSWORD": " " * 20},
    {"POSTGRES_PASSWORD": PASSWORD}, {"APP_DATABASE_NAME": "jobtracker_db"},
    {"APP_DATABASE_NAME": NAME}, {"APP_DATABASE_PORT": "5432"},
    {"APP_DATABASE_USER": "jobtracker"}, {"DEMO_DATABASE_NAME": "jobtracker_db"},
    {"DEMO_DATABASE_HOST": "localhost"}, {"DEMO_DATABASE_PORT": "5432"},
    {"MIGRATION_DATABASE_HOST": "127.0.0.1"},
])
def test_invalid_or_conflicting_demo_config_fails_before_engine_or_connection(change):
    values = demo_values() | change
    with patch("app.database_engine.create_engine") as factory, patch("psycopg.connect") as connect:
        with pytest.raises(DatabaseConfigurationError) as error:
            select_database_engine(values)
    factory.assert_not_called()
    connect.assert_not_called()
    assert PASSWORD not in str(error.value) + repr(error.value)


@pytest.mark.parametrize("migration_name", [None, "jobtracker_db", "postgres", "wrong_demo"])
def test_demo_migration_requires_explicit_matching_name(migration_name):
    values = demo_values()
    if migration_name is not None:
        values["MIGRATION_DATABASE_NAME"] = migration_name
    with patch.dict(os.environ, values, clear=True), patch("dotenv.load_dotenv") as dotenv, \
            patch("psycopg.connect") as connect:
        with pytest.raises(RuntimeError, match="MIGRATION_DATABASE_NAME") as error:
            migration_url()
    dotenv.assert_not_called()
    connect.assert_not_called()
    assert PASSWORD not in str(error.value)


def test_demo_runtime_ignores_ordinary_dotenv_and_creates_no_connection():
    module = ROOT / "backend/app/database.py"
    with patch.dict(os.environ, demo_values(), clear=True), \
            patch("dotenv.load_dotenv") as dotenv, \
            patch("app.database_engine.create_engine") as factory, \
            patch("psycopg.connect") as connect:
        runpy.run_path(str(module))
    dotenv.assert_not_called()
    factory.assert_called_once()
    connect.assert_not_called()


def test_demo_mode_loaded_only_from_dotenv_is_rejected_before_engine_creation():
    module = ROOT / "backend/app/database.py"

    def fake_dotenv(*args, **kwargs):
        os.environ.update(demo_values())

    with patch.dict(os.environ, {}, clear=True), \
            patch("dotenv.load_dotenv", side_effect=fake_dotenv), \
            patch("app.database_engine.create_engine") as factory, \
            patch("psycopg.connect") as connect:
        with pytest.raises(DatabaseConfigurationError, match="must be exported"):
            runpy.run_path(str(module))
    factory.assert_not_called()
    connect.assert_not_called()


def test_demo_auth_settings_also_ignore_ordinary_dotenv():
    from app.auth_config import get_auth_settings

    get_auth_settings.cache_clear()
    try:
        with patch.dict(os.environ, demo_values(), clear=True), \
                patch("app.auth_config.load_dotenv") as dotenv:
            assert get_auth_settings().environment == "development"
        dotenv.assert_not_called()
    finally:
        get_auth_settings.cache_clear()


def test_demo_compose_is_separate_and_project_scoped():
    source = (ROOT / "compose.demo-db.yaml").read_text()
    compose = yaml.safe_load(source)
    assert compose["name"] == "jobtracker-demo-${DEMO_RUN_ID:?Set a fresh 16-character lowercase hex DEMO_RUN_ID}"
    assert set(compose["services"]) == {"demo_db"}
    service = compose["services"]["demo_db"]
    assert service["environment"] == {
        "POSTGRES_USER": "jobtracker_demo",
        "POSTGRES_PASSWORD": "${DEMO_POSTGRES_PASSWORD:?Set a private demo-only database password}",
        "POSTGRES_DB": "jobtracker_demo_${DEMO_RUN_ID}",
    }
    assert service["ports"] == ["127.0.0.1:55434:5432"]
    assert service["volumes"] == ["demo_pgdata:/var/lib/postgresql/data"]
    assert compose["volumes"] == {"demo_pgdata": None}
    assert "container_name" not in service
    assert "depends_on" not in service and "networks" not in service
    assert "include" not in compose and "extends" not in service
    assert "jobtracker_db" not in source and "postgres_data" not in source
    assert "job-tracker-db" not in source
