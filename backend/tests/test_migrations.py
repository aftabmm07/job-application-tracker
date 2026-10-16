from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from app.migration_safety import BASELINE, migration_url, verify_legacy_schema


@pytest.fixture
def connection(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'migration.sqlite'}")
    with engine.begin() as connection:
        assert connection.scalar(text("PRAGMA foreign_keys")) == 1
        yield connection
    engine.dispose()


def configuration(connection):
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.attributes["connection"] = connection
    return config


def test_fresh_upgrade_and_metadata_match(connection):
    config = configuration(connection)
    command.upgrade(config, "head")
    assert set(inspect(connection).get_table_names()) == {"job_applications", "users", "sessions", "alembic_version"}
    assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0002_auth_models"
    command.check(config)
    indexes = {index["name"] for index in inspect(connection).get_indexes("sessions")}
    assert {"ix_sessions_token_hash", "ix_sessions_user_id", "ix_sessions_expires_at", "ix_sessions_idle_expires_at"} <= indexes
    with pytest.raises(IntegrityError):
        connection.execute(text("INSERT INTO job_applications (company_name, job_title, status, user_id) VALUES ('Company', 'Engineer', 'saved', 999)"))


def create_legacy(connection):
    config = configuration(connection)
    command.upgrade(config, BASELINE)
    # This is a newly created temporary SQLite fixture, not an existing user DB.
    connection.execute(text("DROP TABLE alembic_version"))
    connection.execute(text("""INSERT INTO job_applications
        (id, company_name, job_title, location, status, application_date, job_description, created_at)
        VALUES (7, 'Café & Co', 'Engineer', NULL, 'applied', '2025-01-31', 'Original description', '2025-02-01 12:34:56'),
               (12, 'Another company', 'Analyst', 'Berlin', 'saved', NULL, NULL, '2026-01-01 00:00:00')"""))
    return config


def test_legacy_requires_explicit_verified_stamp_and_preserves_rows(connection):
    config = create_legacy(connection)
    before = connection.execute(text("SELECT * FROM job_applications ORDER BY id")).all()
    with pytest.raises(RuntimeError, match="explicitly stamped"):
        command.upgrade(config, "head")
    assert "alembic_version" not in inspect(connection).get_table_names()
    verify_legacy_schema(connection)
    command.stamp(config, BASELINE)
    command.upgrade(config, "head")
    after = connection.execute(text("SELECT id, company_name, job_title, location, status, application_date, job_description, created_at FROM job_applications ORDER BY id")).all()
    assert after == before
    assert connection.scalar(text("SELECT count(*) FROM job_applications WHERE user_id IS NULL")) == len(before)
    assert connection.scalar(text("SELECT count(*) FROM users")) == 0
    assert connection.scalar(text("SELECT count(*) FROM sessions")) == 0
    command.check(config)
    command.upgrade(config, "head")  # Re-running is a no-op.
    assert connection.scalar(text("SELECT count(*) FROM job_applications")) == len(before)


@pytest.mark.parametrize("drift", [
    "ALTER TABLE job_applications ADD COLUMN unexpected TEXT",
    "CREATE TABLE unrelated (id INTEGER)",
    "CREATE INDEX unexpected ON job_applications (company_name)",
    "ALTER TABLE job_applications RENAME COLUMN job_title TO title",
])
def test_schema_drift_blocks_stamp(connection, drift):
    config = create_legacy(connection)
    connection.execute(text(drift))
    with pytest.raises(RuntimeError):
        command.stamp(config, BASELINE)
    assert "alembic_version" not in inspect(connection).get_table_names()


def test_stamp_head_cannot_skip_authentication_migration(connection):
    config = create_legacy(connection)
    with pytest.raises(RuntimeError, match="explicitly stamped"):
        command.stamp(config, "head")


def test_downgrade_is_disabled(connection):
    config = configuration(connection)
    command.upgrade(config, "head")
    with pytest.raises(RuntimeError, match="intentionally disabled"):
        command.downgrade(config, BASELINE)
    assert "user_id" in {column["name"] for column in inspect(connection).get_columns("job_applications")}


@pytest.mark.parametrize("name", [None, "jobtracker_db", "postgres", "template0", "template1"])
def test_migration_target_is_explicit_and_blocks_original(monkeypatch, name):
    monkeypatch.setattr("app.migration_safety.load_dotenv", lambda *args, **kwargs: None)
    if name is None:
        monkeypatch.delenv("MIGRATION_DATABASE_NAME", raising=False)
    else:
        monkeypatch.setenv("MIGRATION_DATABASE_NAME", name)
    with pytest.raises(RuntimeError, match="explicit non-original"):
        migration_url()


@pytest.mark.parametrize('kind', ['BIGINT', 'SMALLINT', 'INT', 'CHAR(150)'])
def test_incompatible_legacy_type_blocks_stamp_and_upgrade_without_changing_rows(connection, kind):
    id_type = kind if kind != 'CHAR(150)' else 'INTEGER'
    company_type = kind if kind == 'CHAR(150)' else 'VARCHAR(150)'
    connection.execute(text(f"""CREATE TABLE job_applications (
        id {id_type} NOT NULL PRIMARY KEY,
        company_name {company_type} NOT NULL, job_title VARCHAR(200) NOT NULL,
        location VARCHAR(150), status VARCHAR(30) NOT NULL, application_date DATE,
        job_description TEXT, created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL
    )"""))
    connection.execute(text("""INSERT INTO job_applications (id, company_name, job_title, status)
        VALUES (7, 'Preserved', 'Original', 'saved')"""))
    before = connection.execute(text('SELECT * FROM job_applications')).all()
    config = configuration(connection)
    for operation in (lambda: command.stamp(config, BASELINE), lambda: command.upgrade(config, 'head')):
        with pytest.raises(RuntimeError, match='baseline type/nullability'):
            operation()
        assert 'alembic_version' not in inspect(connection).get_table_names()
        assert connection.execute(text('SELECT * FROM job_applications')).all() == before


def test_schema_drift_after_verified_stamp_blocks_upgrade(connection):
    config = create_legacy(connection)
    command.stamp(config, BASELINE)
    connection.execute(text('ALTER TABLE job_applications ADD COLUMN unexpected TEXT'))
    with pytest.raises(RuntimeError):
        command.upgrade(config, 'head')
    assert connection.scalar(text('SELECT version_num FROM alembic_version')) == BASELINE
    assert 'users' not in inspect(connection).get_table_names()
    assert connection.scalar(text('SELECT count(*) FROM job_applications')) == 2


@pytest.mark.parametrize('destination', [BASELINE, 'head'])
def test_empty_database_cannot_be_stamped_without_schema(connection, destination):
    with pytest.raises(RuntimeError):
        command.stamp(configuration(connection), destination)
    if 'alembic_version' in inspect(connection).get_table_names():
        assert connection.scalar(text('SELECT count(*) FROM alembic_version')) == 0


def test_stamped_baseline_cannot_be_stamped_to_unapplied_head(connection):
    config = create_legacy(connection)
    command.stamp(config, BASELINE)
    with pytest.raises(RuntimeError, match='verified'):
        command.stamp(config, 'head')
    assert connection.scalar(text('SELECT version_num FROM alembic_version')) == BASELINE
