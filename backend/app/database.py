
import os
import re
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from sqlalchemy.orm import sessionmaker

PROJECT_ROOT = Path(__file__).resolve().parents[2]

load_dotenv(PROJECT_ROOT / ".env")


class DatabaseConfigurationError(ValueError):
    """Runtime target errors contain setting names, never supplied values."""


def runtime_database_name(values):
    name = values.get("APP_DATABASE_NAME")
    if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", name):
        raise DatabaseConfigurationError(
            "APP_DATABASE_NAME is required and must contain 1-63 lowercase ASCII "
            "letters, digits or underscores, starting with a letter"
        )
    if name in {"jobtracker_db", "postgres", "template0", "template1"}:
        raise DatabaseConfigurationError("APP_DATABASE_NAME selects a forbidden database")
    return name


# Fail before engine creation, including during normal app.main/Uvicorn imports.
# No environment has a fallback target; production must also select explicitly.
database_name = runtime_database_name(os.environ)
password = os.getenv("POSTGRES_PASSWORD")

if not password:
    raise RuntimeError("POSTGRES_PASSWORD is not configured")

DATABASE_URL = URL.create(
    drivername="postgresql+psycopg",
    username="jobtracker",
    password=password,
    host="127.0.0.1",
    port=5432,
    database=database_name,
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
)
