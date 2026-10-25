"""Engine selection without environment loading or import-time engine creation."""
from sqlalchemy import create_engine
from sqlalchemy.engine import URL

from app.database_config import (
    DEMO_HOST, DEMO_PORT, DEMO_USER, DatabaseConfigurationError,
    demo_database_target, runtime_database_mode, runtime_database_name,
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
    name = runtime_database_name(values)
    password = values.get('POSTGRES_PASSWORD')
    if not password:
        raise RuntimeError('POSTGRES_PASSWORD is not configured')
    url = URL.create('postgresql+psycopg', username='jobtracker', password=password,
                     host='127.0.0.1', port=5432, database=name)
    return url, create_engine(url, pool_pre_ping=True)
