"""Explicit targets and read-only verification before adopting a legacy database."""
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import Date, DateTime, Integer, String, Text, inspect, text
from sqlalchemy.engine import URL

BASELINE = "0001_legacy"


def migration_url():
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    name = os.getenv("MIGRATION_DATABASE_NAME", "")
    if not name or name in {"jobtracker_db", "postgres", "template0", "template1"}:
        raise RuntimeError("An explicit non-original MIGRATION_DATABASE_NAME is required; jobtracker_db is blocked")
    password = os.getenv("POSTGRES_PASSWORD")
    if not password:
        raise RuntimeError("POSTGRES_PASSWORD is required")
    return URL.create(
        "postgresql+psycopg", username=os.getenv("MIGRATION_DATABASE_USER", "jobtracker"),
        password=password, host=os.getenv("MIGRATION_DATABASE_HOST", "127.0.0.1"),
        port=int(os.getenv("MIGRATION_DATABASE_PORT", "5432")), database=name,
    )


def check_target(connection, expected_name=None):
    if connection.dialect.name == "postgresql":
        if not expected_name or expected_name == "jobtracker_db":
            raise RuntimeError("Explicit non-original PostgreSQL target required")
        actual = connection.scalar(text("SELECT current_database()"))
        if actual != expected_name or actual == "jobtracker_db":
            raise RuntimeError("PostgreSQL migration target does not match the approved database")
        if connection.scalar(text("SELECT current_schema()")) != "public":
            raise RuntimeError("Migration rehearsal requires the public schema")
    elif connection.dialect.name != "sqlite":
        raise RuntimeError("Only PostgreSQL and SQLite migration targets are supported")


def verify_legacy_schema(connection):
    """Fail closed on schema drift. This function never stamps or changes records."""
    inspector = inspect(connection)
    tables = set(inspector.get_table_names()) - {"alembic_version"}
    if tables != {"job_applications"} or inspector.get_view_names():
        raise RuntimeError("Legacy schema must contain only the expected application table")
    columns = {column["name"]: column for column in inspector.get_columns("job_applications")}
    expected = {
        "id": (Integer, None, False), "company_name": (String, 150, False),
        "job_title": (String, 200, False), "location": (String, 150, True),
        "status": (String, 30, False), "application_date": (Date, None, True),
        "job_description": (Text, None, True), "created_at": (DateTime, None, False),
    }
    if columns.keys() != expected.keys():
        raise RuntimeError("Legacy application columns do not match the baseline")
    for name, (kind, length, nullable) in expected.items():
        column = columns[name]
        if not isinstance(column["type"], kind) or column["nullable"] != nullable:
            raise RuntimeError(f"Legacy column {name} does not match its baseline type/nullability")
        if length is not None and getattr(column["type"], "length", None) != length:
            raise RuntimeError(f"Legacy column {name} has an unexpected length")
        default = column["default"]
        if name == "created_at":
            if str(default).lower() not in {"now()", "current_timestamp", "current_timestamp()"}:
                raise RuntimeError("Legacy creation timestamp default differs from baseline")
            if connection.dialect.name == "postgresql" and not column["type"].timezone:
                raise RuntimeError("Legacy creation timestamp must include timezone")
        elif name == "id" and connection.dialect.name == "postgresql":
            if not str(default).startswith("nextval('job_applications_id_seq'"):
                raise RuntimeError("Legacy ID must use the original serial sequence")
        elif default is not None:
            raise RuntimeError(f"Legacy column {name} has an unexpected default")
    if inspector.get_pk_constraint("job_applications")["constrained_columns"] != ["id"]:
        raise RuntimeError("Legacy primary key differs from baseline")
    if (inspector.get_foreign_keys("job_applications") or inspector.get_indexes("job_applications")
            or inspector.get_unique_constraints("job_applications") or inspector.get_check_constraints("job_applications")):
        raise RuntimeError("Legacy constraints/indexes differ from baseline")
    if connection.dialect.name == "postgresql":
        triggers = connection.scalar(text("SELECT count(*) FROM pg_trigger WHERE tgrelid = 'public.job_applications'::regclass AND NOT tgisinternal"))
        if triggers:
            raise RuntimeError("Legacy table has unexpected triggers")
