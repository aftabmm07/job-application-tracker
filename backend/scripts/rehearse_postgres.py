"""Explicit, non-destructive rehearsal on NEW named temporary databases only.

Run only after approval. Creates two databases, copies the restored backup via a
stream, verifies/stamps the legacy baseline, then upgrades. Never drops databases
or changes existing application records. No connection to jobtracker_db is made.
"""
import hashlib
from pathlib import Path
import subprocess

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from app.migration_safety import BASELINE, check_target, migration_url, verify_legacy_schema

FRESH = "jobtracker_auth_fresh_9b2"
LEGACY = "jobtracker_auth_legacy_9b2"
SOURCE = "jobtracker_restore_test"
CONTAINER = "job-tracker-db"


def docker_psql(sql):
    return subprocess.run(
        ["docker", "exec", CONTAINER, "psql", "-U", "jobtracker", "-d", "postgres", "-v", "ON_ERROR_STOP=1", "-At", "-c", sql],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


def fingerprint(connection):
    # Explicit legacy columns exclude the newly added owner column. Print only
    # a count/digest, never descriptions or other application contents.
    rows = connection.execute(text("""SELECT row_to_json(legacy)::text FROM
        (SELECT id, company_name, job_title, location, status, application_date,
                job_description, created_at FROM job_applications ORDER BY id) legacy"""))
    digest = hashlib.sha256()
    count = 0
    for row in rows:
        digest.update(row[0].encode("utf-8") + b"\n")
        count += 1
    return count, digest.hexdigest()


def config_for(connection, name):
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.attributes.update(connection=connection, target_name=name)
    return config


def main():
    print(f"Temporary migration targets: {FRESH}, {LEGACY}; read-only source: {SOURCE}")
    print("Original jobtracker_db is blocked. No database cleanup/drop will run.")
    # migration_url requires an explicit allowed target; never use the app engine.
    import os
    if os.getenv("MIGRATION_DATABASE_NAME") != FRESH:
        raise RuntimeError(f"Set MIGRATION_DATABASE_NAME={FRESH} explicitly")
    url = migration_url()
    existing = set(docker_psql("SELECT datname FROM pg_database").splitlines())
    if FRESH in existing or LEGACY in existing:
        raise RuntimeError("A rehearsal target already exists; refusing to modify it or overwrite evidence")
    if SOURCE not in existing:
        raise RuntimeError("Required read-only restored source database is unavailable")
    source_engine = create_engine(url.set(database=SOURCE))
    try:
        with source_engine.connect() as connection:
            check_target(connection, SOURCE)
            verify_legacy_schema(connection)
            source_before = fingerprint(connection)
    finally:
        source_engine.dispose()

    for name in (FRESH, LEGACY):
        print(f"CREATE DATABASE {name} (new temporary database)")
        docker_psql(f'CREATE DATABASE "{name}" OWNER jobtracker')

    # Stream a NEW dump from the restored test database; never open or alter the
    # existing backup artifact. No --clean or --create options are used.
    print(f"Copying {SOURCE} into {LEGACY} using pg_dump | pg_restore")
    dump = subprocess.Popen(["docker", "exec", CONTAINER, "pg_dump", "-U", "jobtracker", "-d", SOURCE,
                             "--format=custom", "--no-owner", "--no-privileges"], stdout=subprocess.PIPE)
    try:
        restore = subprocess.run(["docker", "exec", "-i", CONTAINER, "pg_restore", "-U", "jobtracker",
                                  "-d", LEGACY, "--no-owner", "--no-privileges", "--exit-on-error"], stdin=dump.stdout)
    finally:
        dump.stdout.close()
    if dump.wait() != 0 or restore.returncode != 0:
        raise RuntimeError("Restored-copy streaming failed; temporary databases retained for inspection")

    for name in (FRESH, LEGACY):
        engine = create_engine(url.set(database=name))
        try:
            with engine.begin() as connection:
                check_target(connection, name)
                config = config_for(connection, name)
                if name == LEGACY:
                    verify_legacy_schema(connection)
                    before = fingerprint(connection)
                    if before != source_before:
                        raise RuntimeError("Legacy clone does not match the restored source")
                    print(f"{name}: schema verified; explicitly stamping {BASELINE}")
                    command.stamp(config, BASELINE)
                print(f"{name}: alembic upgrade head")
                command.upgrade(config, "head")
                command.check(config)
                if name == LEGACY:
                    after = fingerprint(connection)
                    if after != before:
                        raise RuntimeError("Legacy record fingerprint changed")
                    unowned = connection.scalar(text("SELECT count(*) FROM job_applications WHERE user_id IS NULL"))
                    if unowned != before[0]:
                        raise RuntimeError("Legacy ownership was unexpectedly assigned")
                    print(f"{name}: preserved {after[0]} records; SHA-256 {after[1]}; all owners NULL")
                for table in ("users", "sessions"):
                    if connection.scalar(text(f"SELECT count(*) FROM {table}")) != 0:
                        raise RuntimeError("Authentication tables unexpectedly populated")
        finally:
            engine.dispose()
    source_engine = create_engine(url.set(database=SOURCE))
    try:
        with source_engine.connect() as connection:
            if fingerprint(connection) != source_before:
                raise RuntimeError("Restored source fingerprint changed")
    finally:
        source_engine.dispose()
    print("Both PostgreSQL rehearsals passed. Source unchanged; temporary databases retained.")


if __name__ == "__main__":
    main()
