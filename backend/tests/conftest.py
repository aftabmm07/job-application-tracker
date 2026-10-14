"""Match PostgreSQL foreign-key enforcement in every SQLite-backed test."""
import os
import sqlite3

from sqlalchemy import event
from sqlalchemy.engine import Engine

# Test collection imports app.main. This names its otherwise unused runtime
# engine; API fixtures still override get_db with isolated SQLite connections.
os.environ.setdefault("APP_DATABASE_NAME", "jobtracker_auth_ci")


@event.listens_for(Engine, "connect")
def enable_sqlite_foreign_keys(dbapi_connection, connection_record):
    if isinstance(dbapi_connection, sqlite3.Connection):
        previous = dbapi_connection.autocommit
        dbapi_connection.autocommit = True
        try:
            dbapi_connection.execute("PRAGMA foreign_keys=ON")
        finally:
            dbapi_connection.autocommit = previous
