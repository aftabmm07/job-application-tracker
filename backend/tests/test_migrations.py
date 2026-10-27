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
    assert set(inspect(connection).get_table_names()) == {"job_applications", "users", "sessions", "email_verification_tokens", "email_verification_outbox", "alembic_version"}
    assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0005_user_profile"
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


def seed_authentication_records(connection):
    """Synthetic records in the temporary SQLite database, before revision 0003."""
    connection.execute(text("""INSERT INTO users (id, email, password_hash)
        VALUES (1, 'migration@example.com', '$argon2id$synthetic-test-hash')"""))
    connection.execute(text("""INSERT INTO sessions
        (token_hash, user_id, created_at, expires_at, last_activity_at, idle_expires_at)
        VALUES (:digest, 1, '2030-01-01 00:00:00', '2030-01-01 02:00:00',
                '2030-01-01 00:00:00', '2030-01-01 01:00:00')"""), {"digest": b's' * 32})
    connection.execute(text("""INSERT INTO job_applications
        (company_name, job_title, status, user_id)
        VALUES ('Owned', 'Engineer', 'applied', 1), ('Legacy', 'Analyst', 'saved', NULL)"""))


def test_upgrade_from_0002_preserves_all_existing_records(connection):
    config = configuration(connection)
    command.upgrade(config, "0002_auth_models")
    seed_authentication_records(connection)
    tables = ("users", "sessions", "job_applications")
    before = {table: connection.execute(text(f"SELECT * FROM {table} ORDER BY id")).all()
              for table in tables}
    user_columns = "id, email, password_hash, created_at, is_active, email_verified_at"
    before["users"] = connection.execute(text(f"SELECT {user_columns} FROM users ORDER BY id")).all()
    command.upgrade(config, "head")
    assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0005_user_profile"
    assert connection.scalar(text("SELECT count(*) FROM email_verification_tokens")) == 0
    assert connection.scalar(text("SELECT count(*) FROM email_verification_outbox")) == 0
    for table in tables:
        columns = user_columns if table == "users" else "*"
        assert connection.execute(text(f"SELECT {columns} FROM {table} ORDER BY id")).all() == before[table]
    command.check(config)
    command.upgrade(config, "head")
    for table in tables:
        columns = user_columns if table == "users" else "*"
        assert connection.execute(text(f"SELECT {columns} FROM {table} ORDER BY id")).all() == before[table]


def test_verification_migration_schema_matches_model(connection):
    from app.models import EmailVerificationToken
    from sqlalchemy import CheckConstraint

    command.upgrade(configuration(connection), "head")
    inspector = inspect(connection)
    table = EmailVerificationToken.__table__
    columns = {column["name"]: column for column in inspector.get_columns(table.name)}
    assert set(columns) == set(table.columns.keys())
    for column in table.columns:
        reflected = columns[column.name]
        assert reflected["nullable"] == column.nullable
        assert reflected["default"] is None
        assert str(reflected["type"].compile(dialect=connection.dialect)) == str(column.type.compile(dialect=connection.dialect))
    assert inspector.get_pk_constraint(table.name)["constrained_columns"] == ["id"]
    assert {item["name"]: (item["column_names"], bool(item["unique"]))
            for item in inspector.get_indexes(table.name)} == {
        "ix_email_verification_tokens_token_hash": (["token_hash"], True),
        "ix_email_verification_tokens_user_id": (["user_id"], False),
    }
    foreign_keys = inspector.get_foreign_keys(table.name)
    assert len(foreign_keys) == 1
    assert foreign_keys[0]["constrained_columns"] == ["user_id"]
    assert foreign_keys[0]["referred_table"] == "users"
    assert foreign_keys[0]["referred_columns"] == ["id"]
    assert foreign_keys[0]["options"]["ondelete"] == "CASCADE"
    assert {item["name"]: item["sqltext"] for item in inspector.get_check_constraints(table.name)} == {
        constraint.name: str(constraint.sqltext)
        for constraint in table.constraints if isinstance(constraint, CheckConstraint)
    }


def insert_verification(connection, **overrides):
    values = dict(user_id=1, token_hash=b'v' * 32, created_at='2030-01-01 00:00:00',
                  expires_at='2030-01-01 01:00:00', consumed_at=None)
    values.update(overrides)
    connection.execute(text("""INSERT INTO email_verification_tokens
        (user_id, token_hash, created_at, expires_at, consumed_at)
        VALUES (:user_id, :token_hash, :created_at, :expires_at, :consumed_at)"""), values)


@pytest.mark.parametrize("overrides", [
    {"user_id": 999}, {"user_id": None}, {"token_hash": None},
    {"token_hash": b'x' * 31}, {"token_hash": b'x' * 33},
    {"created_at": None}, {"expires_at": None},
    {"expires_at": '2030-01-01 00:00:00'},
    {"expires_at": '2029-12-31 23:59:59'},
])
def test_migrated_verification_constraints_reject_invalid_rows(connection, overrides):
    command.upgrade(configuration(connection), "head")
    seed_authentication_records(connection)
    with pytest.raises(IntegrityError):
        with connection.begin_nested():
            insert_verification(connection, **overrides)
    assert connection.scalar(text("SELECT count(*) FROM email_verification_tokens")) == 0


def test_migrated_verification_uniqueness_cascade_and_idempotence(connection):
    config = configuration(connection)
    command.upgrade(config, "head")
    connection.execute(text("""INSERT INTO users (id, email, password_hash)
        VALUES (1, 'tokens@example.com', '$argon2id$synthetic-test-hash')"""))
    insert_verification(connection)
    with pytest.raises(IntegrityError):
        with connection.begin_nested():
            insert_verification(connection)
    insert_verification(connection, token_hash=b'w' * 32, consumed_at='2030-01-01 00:30:00')
    before = connection.execute(text("SELECT * FROM email_verification_tokens ORDER BY id")).all()
    command.upgrade(config, "head")
    assert connection.execute(text("SELECT * FROM email_verification_tokens ORDER BY id")).all() == before
    with pytest.raises(RuntimeError, match="intentionally disabled"):
        command.downgrade(config, "0002_auth_models")
    assert connection.execute(text("SELECT * FROM email_verification_tokens ORDER BY id")).all() == before
    connection.execute(text("DELETE FROM users WHERE id = 1"))
    assert connection.scalar(text("SELECT count(*) FROM email_verification_tokens")) == 0


def test_outbox_upgrade_from_0003_preserves_existing_records(connection):
    config = configuration(connection)
    command.upgrade(config, "0003_email_verification_tokens")
    seed_authentication_records(connection)
    insert_verification(connection)
    before = {
        table: connection.execute(text(f"SELECT * FROM {table} ORDER BY id")).all()
        for table in ("users", "sessions", "job_applications", "email_verification_tokens")
    }
    command.upgrade(config, "0004_email_verification_outbox")
    assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0004_email_verification_outbox"
    assert connection.scalar(text("SELECT count(*) FROM email_verification_outbox")) == 0
    for table, rows in before.items():
        assert connection.execute(text(f"SELECT * FROM {table} ORDER BY id")).all() == rows
    command.upgrade(config, "head")
    command.check(config)


def test_profile_upgrade_from_0004_preserves_existing_records(connection):
    from app.models import User

    config = configuration(connection)
    command.upgrade(config, "0004_email_verification_outbox")
    seed_authentication_records(connection)
    insert_verification(connection)
    insert_outbox(connection)
    tables = ("users", "sessions", "job_applications",
              "email_verification_tokens", "email_verification_outbox")
    before = {table: connection.execute(text(f"SELECT * FROM {table} ORDER BY id")).all()
              for table in tables}
    old_user_columns = [column.name for column in User.__table__.columns
                        if column.name not in {"full_name", "professional_headline", "location",
                                               "linkedin_url", "github_url", "portfolio_url"}]
    command.upgrade(config, "head")
    assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0005_user_profile"
    for table in tables:
        columns = ", ".join(old_user_columns) if table == "users" else "*"
        assert connection.execute(text(f"SELECT {columns} FROM {table} ORDER BY id")).all() == before[table]
    assert connection.execute(text("SELECT full_name, professional_headline, location, "
                                   "linkedin_url, github_url, portfolio_url FROM users")).one() == (None,) * 6
    inspector = inspect(connection)
    actual = {column["name"]: column for column in inspector.get_columns("users")}
    for column in User.__table__.columns:
        assert actual[column.name]["nullable"] == column.nullable
        assert str(actual[column.name]["type"].compile(dialect=connection.dialect)) == str(
            column.type.compile(dialect=connection.dialect))
    command.check(config)
    command.upgrade(config, "head")


def test_outbox_migration_schema_matches_model(connection):
    from app.models import EmailVerificationOutbox
    from sqlalchemy import CheckConstraint

    command.upgrade(configuration(connection), "head")
    table = EmailVerificationOutbox.__table__
    inspector = inspect(connection)
    columns = {column["name"]: column for column in inspector.get_columns(table.name)}
    assert set(columns) == set(table.columns.keys())
    for column in table.columns:
        assert columns[column.name]["nullable"] == column.nullable
        assert str(columns[column.name]["type"].compile(dialect=connection.dialect)) == str(
            column.type.compile(dialect=connection.dialect)
        )
    assert inspector.get_pk_constraint(table.name)["constrained_columns"] == ["id"]
    assert {item["name"]: item["column_names"] for item in inspector.get_indexes(table.name)} == {
        "ix_email_verification_outbox_due": ["state", "next_attempt_at"],
        "ix_email_verification_outbox_lease": ["state", "lease_expires_at"],
    }
    assert inspector.get_unique_constraints(table.name)[0]["column_names"] == ["token_id"]
    foreign_key, = inspector.get_foreign_keys(table.name)
    assert foreign_key["constrained_columns"] == ["token_id"]
    assert foreign_key["referred_table"] == "email_verification_tokens"
    assert foreign_key["options"]["ondelete"] == "CASCADE"
    assert {item["name"]: item["sqltext"] for item in inspector.get_check_constraints(table.name)} == {
        constraint.name: str(constraint.sqltext)
        for constraint in table.constraints if isinstance(constraint, CheckConstraint)
    }


def insert_outbox(connection, **overrides):
    values = dict(token_id=1, nonce=b"n" * 32, key_version="v1", state="queued",
                  created_at="2030-01-01 00:00:00", next_attempt_at="2030-01-01 00:00:00",
                  expires_at="2030-01-01 01:00:00", attempts=0, lease_expires_at=None,
                  lease_owner=None, fencing_generation=0, failure_category=None)
    values.update(overrides)
    connection.execute(text("""INSERT INTO email_verification_outbox
        (token_id, nonce, key_version, state, created_at, next_attempt_at, expires_at,
         attempts, lease_expires_at, lease_owner, fencing_generation, failure_category)
        VALUES (:token_id, :nonce, :key_version, :state, :created_at, :next_attempt_at,
                :expires_at, :attempts, :lease_expires_at, :lease_owner,
                :fencing_generation, :failure_category)"""), values)


@pytest.mark.parametrize("overrides", [
    {"token_id": 999}, {"token_id": None}, {"nonce": b"n" * 31},
    {"key_version": ""}, {"state": "unknown"}, {"attempts": -1},
    {"fencing_generation": -1}, {"expires_at": "2030-01-01 00:00:00"},
    {"next_attempt_at": "2029-12-31 23:59:59"},
    {"state": "leased"}, {"lease_owner": b"x" * 16},
    {"failure_category": "raw exception text"},
])
def test_migrated_outbox_constraints_reject_invalid_rows(connection, overrides):
    command.upgrade(configuration(connection), "head")
    seed_authentication_records(connection)
    insert_verification(connection)
    with pytest.raises(IntegrityError):
        with connection.begin_nested():
            insert_outbox(connection, **overrides)
    assert connection.scalar(text("SELECT count(*) FROM email_verification_outbox")) == 0


def test_migrated_outbox_unique_token_and_cascade(connection):
    command.upgrade(configuration(connection), "head")
    seed_authentication_records(connection)
    insert_verification(connection)
    insert_outbox(connection)
    with pytest.raises(IntegrityError):
        with connection.begin_nested():
            insert_outbox(connection)
    connection.execute(text("DELETE FROM email_verification_tokens WHERE id = 1"))
    assert connection.scalar(text("SELECT count(*) FROM email_verification_outbox")) == 0
