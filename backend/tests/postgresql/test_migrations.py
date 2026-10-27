"""Real PostgreSQL migration adoption, with synthetic legacy records only."""
from alembic import command
import pytest
from sqlalchemy import inspect, text

from app.migration_safety import BASELINE, verify_legacy_schema


@pytest.mark.parametrize('case', ['fresh', 'legacy', 'bigint', 'char', 'timestamp'])
def test_postgresql_migrations(pg_target, case):
    engine = pg_target.engine(case)
    try:
        with engine.begin() as connection:
            assert not inspect(connection).get_table_names(), 'Single-use migration database must be empty'
            config = pg_target.migration_config(connection, case)
            if case == 'fresh':
                command.upgrade(config, 'head')
                command.check(config)
                assert connection.scalar(text('SELECT version_num FROM alembic_version')) == '0005_user_profile'
                assert set(inspect(connection).get_table_names()) == {
                    'alembic_version', 'job_applications', 'users', 'sessions',
                    'email_verification_tokens', 'email_verification_outbox',
                }
                return
            command.upgrade(config, BASELINE)
            connection.execute(text('DROP TABLE alembic_version'))  # Only synthetic single-use DB.
            connection.execute(text("""INSERT INTO job_applications
                (id, company_name, job_title, status, application_date, created_at)
                VALUES (7, 'Café', 'Engineer', 'applied', '2025-12-31', '2026-01-01T02:00:00+02:00'),
                       (12, 'Preserved', 'Analyst', 'saved', NULL, '2026-01-02T00:00:00Z')"""))
            if case == 'bigint':
                connection.execute(text('ALTER TABLE job_applications ALTER COLUMN id TYPE BIGINT'))
            elif case == 'char':
                connection.execute(text('ALTER TABLE job_applications ALTER COLUMN company_name TYPE CHAR(150)'))
            elif case == 'timestamp':
                connection.execute(text("ALTER TABLE job_applications ALTER COLUMN created_at TYPE TIMESTAMP WITHOUT TIME ZONE USING created_at AT TIME ZONE 'UTC'"))
            before = connection.execute(text('SELECT * FROM job_applications ORDER BY id')).all()
            if case != 'legacy':
                for operation in (lambda: command.stamp(config, BASELINE), lambda: command.upgrade(config, 'head')):
                    with pytest.raises(RuntimeError, match='baseline type/nullability'):
                        operation()
                    assert 'alembic_version' not in inspect(connection).get_table_names()
                    assert connection.execute(text('SELECT * FROM job_applications ORDER BY id')).all() == before
                return
            with pytest.raises(RuntimeError, match='explicitly stamped'):
                command.upgrade(config, 'head')
            verify_legacy_schema(connection)
            command.stamp(config, BASELINE)
            command.upgrade(config, 'head')
            assert connection.execute(text('SELECT id, company_name, job_title, location, status, application_date, job_description, created_at FROM job_applications ORDER BY id')).all() == before
            assert connection.scalar(text('SELECT count(*) FROM job_applications WHERE user_id IS NULL')) == 2
            assert connection.scalar(text('SELECT count(*) FROM users')) == 0
            assert connection.scalar(text('SELECT count(*) FROM sessions')) == 0
            assert connection.scalar(text('SELECT count(*) FROM email_verification_tokens')) == 0
            assert connection.scalar(text('SELECT count(*) FROM email_verification_outbox')) == 0
            command.check(config)
            command.upgrade(config, 'head')  # Idempotent.
    finally:
        engine.dispose()
