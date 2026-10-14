from alembic import context
from sqlalchemy import create_engine, inspect, text

from app.migration_safety import BASELINE, check_target, migration_url, verify_legacy_schema
from app.models import Base

config = context.config


def run(connection, target_name=None):
    check_target(connection, target_name)
    tables = set(inspect(connection).get_table_names())
    current = None
    if "alembic_version" in tables:
        current = connection.scalar(text("SELECT version_num FROM alembic_version"))
    if current is None and tables - {"alembic_version"}:
        verify_legacy_schema(connection)
        try:
            destination = context.get_revision_argument()
        except KeyError:  # Read-only `alembic current` has no destination.
            destination = None
        if destination not in {None, BASELINE}:
            raise RuntimeError("Verified legacy database must be explicitly stamped 0001_legacy before upgrading")
    context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    raise RuntimeError("Offline migrations are disabled: target and schema verification require a connection")
elif config.attributes.get("connection") is not None:
    run(config.attributes["connection"], config.attributes.get("target_name"))
else:
    url = migration_url()
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            run(connection, url.database)
    finally:
        engine.dispose()
