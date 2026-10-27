from alembic import context
from sqlalchemy import create_engine, inspect, text
import os

from app.database_config import stack_database_target
from app.migration_safety import (
    BASELINE, check_stack_target, check_target, migration_url, verify_legacy_schema,
)
from app.models import Base

config = context.config


def run(connection, target_name=None):
    if os.environ.get("APP_DATABASE_MODE") == "stack":
        target = stack_database_target(os.environ)
        if target_name != target.name:
            raise RuntimeError("Stack migration target name does not match the selected run")
        check_stack_target(connection, target)
    else:
        check_target(connection, target_name)
    tables = set(inspect(connection).get_table_names())
    current = None
    if "alembic_version" in tables:
        current = connection.scalar(text("SELECT version_num FROM alembic_version"))
    # A prior stamp is not proof that the schema has remained compatible.
    if current == BASELINE:
        verify_legacy_schema(connection)
    if current is None and tables - {"alembic_version"}:
        verify_legacy_schema(connection)
        try:
            destination = context.get_revision_argument()
        except KeyError:  # Read-only `alembic current` has no destination.
            destination = None
        if destination not in {None, BASELINE}:
            raise RuntimeError("Verified legacy database must be explicitly stamped 0001_legacy before upgrading")
    options = dict(connection=connection, target_metadata=Base.metadata, compare_type=True)
    context.configure(**options)
    migration_fn = context.get_context().opts["fn"]

    def verified_steps(revisions, migration_context):
        # Inspect planned steps before Alembic writes a version row. In particular,
        # an empty DB must not be stamped as though schema creation had run.
        steps = list(migration_fn(revisions, migration_context))
        if any(step.info.is_stamp for step in steps):
            if context.get_revision_argument() != BASELINE:
                raise RuntimeError("Only explicitly verified 0001_legacy stamping is supported")
            verify_legacy_schema(connection)
        return steps

    context.configure(**options, fn=verified_steps)
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
