"""Opt-in PostgreSQL 17 test targets; no connection is made on import.

Never use migration_url(), .env, the runtime engine, or libpq target defaults here.
"""
from dataclasses import dataclass, field
import os
from pathlib import Path
import re

from alembic.config import Config
from psycopg.adapt import AdaptersMap
from sqlalchemy import create_engine, event
from sqlalchemy.engine import URL
from sqlalchemy.pool import NullPool


class UnsafeTestTarget(RuntimeError):
    """Sanitized errors deliberately omit URLs, credentials and server values."""


CASES = ('api', 'fresh', 'legacy', 'bigint', 'char', 'timestamp')


@dataclass(frozen=True)
class TestTarget:
    __test__ = False
    run_id: str
    password: str = field(repr=False)

    @classmethod
    def from_environment(cls, values=None):
        values = os.environ if values is None else values
        if values.get('RUN_ISOLATED_POSTGRES_TESTS') != '1':
            raise UnsafeTestTarget('Isolated PostgreSQL tests require explicit opt-in')
        if values.get('PG_TEST_RECEIPT'):
            from scripts.postgres_test_provenance import read_receipt
            receipt = read_receipt(values['PG_TEST_RECEIPT'])
            run_id, password = receipt['run_id'], receipt['password']
        else:
            # Offline configuration checks may construct targets; execution below
            # always additionally requires a current creation receipt and lease.
            run_id = values.get('PG_TEST_RUN_ID', '')
            password = values.get('PG_TEST_PASSWORD', '')
        if not re.fullmatch(r'[0-9a-f]{32}', run_id):
            raise UnsafeTestTarget('PG_TEST_RUN_ID must be 32 lowercase hex characters')
        if not re.fullmatch(r'[0-9a-f]{64}', password):
            raise UnsafeTestTarget('PG_TEST_PASSWORD must be a fresh 64-character hex test secret')
        return cls(run_id, password)

    def __post_init__(self):
        if not re.fullmatch(r'[0-9a-f]{32}', self.run_id) or not re.fullmatch(r'[0-9a-f]{64}', self.password):
            raise UnsafeTestTarget('Invalid isolated test configuration')

    @property
    def role(self):
        return f'jt613_{self.run_id[:12]}'

    @property
    def container(self):
        return f'jobtracker-pg17-613-{self.run_id}'

    def database(self, case):
        if case not in CASES:
            raise UnsafeTestTarget('Database is not in the disposable allowlist')
        return f'{self.role}_{case}'

    def url(self, case):
        return URL.create('postgresql+psycopg', username=self.role, password=self.password,
                          host='127.0.0.1', port=55433, database=self.database(case))

    def validate_url(self, url):
        if (url.drivername != 'postgresql+psycopg' or url.host != '127.0.0.1'
                or url.port != 55433 or url.username != self.role or url.password != self.password
                or url.database not in {self.database(case) for case in CASES} or url.query):
            raise UnsafeTestTarget('Refusing a non-isolated PostgreSQL test target')

    def authorize(self):
        if TestTarget.from_environment() != self:
            raise UnsafeTestTarget('Disposable test configuration changed')
        if any(name.startswith('PG') and name not in {'PG_TEST_RUN_ID', 'PG_TEST_PASSWORD', 'PG_TEST_RECEIPT', 'PG_TEST_OPERATION_NONCE'}
               for name in os.environ):
            raise UnsafeTestTarget('Remove libpq environment overrides before isolated testing')
        from scripts.postgres_test_provenance import authorize_receipt
        authorize_receipt(self)

    def validate_connect_parameters(self, url, args, params):
        self.authorize()
        self.validate_url(url)
        expected = dict(host='127.0.0.1', port=55433, user=self.role,
                        password=self.password, dbname=url.database,
                        connect_timeout=3, application_name='jobtracker-isolated-tests',
                        options='-c statement_timeout=5000 -c lock_timeout=2000 '
                                '-c idle_in_transaction_session_timeout=15000')
        # Disallow DSNs, libpq service files, hostaddr and extra connection options.
        params = dict(params)
        if 'context' in params:
            # SQLAlchemy 2.1 supplies its Psycopg type adapter map, not libpq options.
            if type(params.pop('context')) is not AdaptersMap:
                raise UnsafeTestTarget('Unexpected PostgreSQL adapter context')
        if args or params != expected:
            raise UnsafeTestTarget('Refusing unexpected PostgreSQL connection parameters')

    def verify_connection(self, connection, database):
        """DBAPI hook: identity is checked before caller SQL, including migrations."""
        self.authorize()
        if database not in {self.database(case) for case in CASES}:
            raise UnsafeTestTarget('Database is not in the disposable allowlist')
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT current_setting('server_version_num')::integer, "
                               "current_setting('jobtracker.test_run', true), current_database(), "
                               "current_user, current_schema(), current_setting('data_directory')")
                version, marker, actual_db, role, schema, data_directory = cursor.fetchone()
            connection.rollback()
            if not (170000 <= version < 180000 and marker == self.run_id and actual_db == database
                    and role == self.role and schema == 'public'
                    and data_directory == '/var/lib/postgresql/data'):
                raise UnsafeTestTarget('PostgreSQL test server identity verification failed')
        except UnsafeTestTarget:
            raise
        except Exception:
            raise UnsafeTestTarget('Unable to verify PostgreSQL test server identity') from None

    def engine(self, case):
        self.authorize()
        url = self.url(case)
        self.validate_url(url)
        engine = create_engine(url, poolclass=NullPool, hide_parameters=True,
                               connect_args=dict(connect_timeout=3, application_name='jobtracker-isolated-tests',
                                                 options='-c statement_timeout=5000 -c lock_timeout=2000 '
                                                         '-c idle_in_transaction_session_timeout=15000'))

        @event.listens_for(engine, 'do_connect')
        def before_connect(dialect, record, args, params):
            self.validate_connect_parameters(engine.url, args, params)
            from scripts.postgres_test_provenance import verify_resources
            verify_resources(self)

        @event.listens_for(engine, 'connect')
        def connected(connection, record):
            try:
                self.verify_connection(connection, url.database)
            except Exception:
                connection.close()
                raise

        return engine

    def migration_config(self, connection, case):
        self.authorize()
        self.validate_url(connection.engine.url)
        if connection.engine.url.database != self.database(case):
            raise UnsafeTestTarget('Migration target mismatch')
        from scripts.postgres_test_provenance import verify_resources
        verify_resources(self)
        # Engine hooks have verified this physical connection before any SQL.
        config = Config(str(Path(__file__).resolve().parents[1] / 'alembic.ini'))
        config.attributes.update(connection=connection, target_name=self.database(case))
        return config
