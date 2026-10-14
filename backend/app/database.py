
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from sqlalchemy.orm import sessionmaker

PROJECT_ROOT = Path(__file__).resolve().parents[2]

load_dotenv(PROJECT_ROOT / ".env")

password = os.getenv("POSTGRES_PASSWORD")

if not password:
    raise RuntimeError("POSTGRES_PASSWORD is not configured")

DATABASE_URL = URL.create(
    drivername="postgresql+psycopg",
    username="jobtracker",
    password=password,
    host="127.0.0.1",
    port=5432,
    # Explicit override for isolated migrated development databases. No migration
    # or connection is performed here; never run this branch on the original DB.
    database=os.getenv("APP_DATABASE_NAME", "jobtracker_db"),
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
)