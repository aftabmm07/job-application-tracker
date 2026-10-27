"""Engine selection without environment loading or import-time engine creation."""
from sqlalchemy import create_engine, event
from sqlalchemy.engine import URL

from app.database_config import (
    DEMO_HOST, DEMO_PORT, DEMO_USER, DatabaseConfigurationError,
    demo_database_target, runtime_database_mode, runtime_database_name,
    stack_database_target,
)


def select_database_engine(values):
    """Return the ordinary URL/engine; disposable selection is deliberately closed.

    An offline receipt preflight is not live authorization. Do not call it here
    or accept its URL as an engine override until the complete operation exists.
    Creating a SQLAlchemy engine does not open a DBAPI connection.
    """
    mode = runtime_database_mode(values)
    if mode == 'disposable':
        raise DatabaseConfigurationError(
            'Disposable engine selection is unavailable pending browser-lease issuance, '
            'live provenance checks and explicit operation authorization'
        )
    if mode == 'demo':
        target = demo_database_target(values)
        url = URL.create('postgresql+psycopg', username=DEMO_USER, password=target.password,
                         host=DEMO_HOST, port=DEMO_PORT, database=target.name)
        return url, create_engine(url, pool_pre_ping=True)
    if mode == 'stack':
        target = stack_database_target(values)
        url = URL.create('postgresql+psycopg', username=target.user, password=target.password,
                         host=target.host, port=target.port, database=target.name)
        engine = create_engine(url, pool_pre_ping=True, hide_parameters=True)

        def verify_stack_identity(dbapi_connection, _record):
            # A new DBAPI connection is checked before it enters the pool.
            # This SELECT never reads application data or changes the schema.
            try:
                with dbapi_connection.cursor() as cursor:
                    cursor.execute('SELECT current_database(), current_user, current_schema()')
                    identity = cursor.fetchone()
                dbapi_connection.rollback()
            except Exception:
                raise DatabaseConfigurationError('Stack database identity could not be verified') from None
            if identity != (target.name, target.user, 'public'):
                raise DatabaseConfigurationError('Stack database identity does not match the selected run')

        event.listen(engine, 'connect', verify_stack_identity)
        return url, engine
    name = runtime_database_name(values)
    password = values.get('POSTGRES_PASSWORD')
    if not password:
        raise RuntimeError('POSTGRES_PASSWORD is not configured')
    url = URL.create('postgresql+psycopg', username='jobtracker', password=password,
                     host='127.0.0.1', port=5432, database=name)
    return url, create_engine(url, pool_pre_ping=True)
