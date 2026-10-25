
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy.orm import sessionmaker

from app.database_config import DatabaseConfigurationError, runtime_database_name
from app.database_engine import select_database_engine

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Demo mode uses only explicit process environment, never the ordinary .env.
if os.environ.get("APP_DATABASE_MODE") != "demo":
    load_dotenv(PROJECT_ROOT / ".env")
    if os.environ.get("APP_DATABASE_MODE") == "demo":
        raise DatabaseConfigurationError("APP_DATABASE_MODE=demo must be exported, not loaded from .env")


# Fail before engine creation, including during normal app.main/Uvicorn imports.
# No environment has a fallback target; production must also select explicitly.
DATABASE_URL, engine = select_database_engine(os.environ)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
)


def get_database_engine():
    """The same selected bind used by application/authentication sessions."""
    return SessionLocal.kw['bind']
