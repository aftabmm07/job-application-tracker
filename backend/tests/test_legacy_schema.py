"""Strict reflected PostgreSQL attributes without connecting to PostgreSQL."""
from unittest.mock import Mock

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from app.migration_safety import verify_legacy_schema


@pytest.fixture
def legacy(monkeypatch):
    connection = Mock()
    connection.dialect = pg.dialect()
    connection.scalar.return_value = 0
    inspector = Mock()
    inspector.get_table_names.return_value = ['job_applications']
    inspector.get_view_names.return_value = []
    inspector.get_materialized_view_names.return_value = []
    inspector.get_pk_constraint.return_value = {'constrained_columns': ['id']}
    for name in ('get_foreign_keys', 'get_indexes', 'get_unique_constraints', 'get_check_constraints'):
        getattr(inspector, name).return_value = []
    types = {'id': pg.INTEGER(), 'company_name': pg.VARCHAR(150), 'job_title': pg.VARCHAR(200),
             'location': pg.VARCHAR(150), 'status': pg.VARCHAR(30), 'application_date': pg.DATE(),
             'job_description': pg.TEXT(), 'created_at': pg.TIMESTAMP(timezone=True)}
    columns = {name: {'name': name, 'type': kind, 'nullable': name in {
        'location', 'application_date', 'job_description'}, 'default': None,
        'autoincrement': name == 'id'} for name, kind in types.items()}
    columns['id']['default'] = "nextval('job_applications_id_seq'::regclass)"
    columns['created_at']['default'] = 'now()'
    inspector.get_columns.return_value = list(columns.values())
    monkeypatch.setattr('app.migration_safety.inspect', lambda _: inspector)
    return connection, inspector, columns


@pytest.mark.parametrize('default', [
    "nextval('job_applications_id_seq'::regclass)",
    "nextval('public.job_applications_id_seq'::regclass)",
    "nextval('\"public\".\"job_applications_id_seq\"'::regclass)",
])
@pytest.mark.parametrize('timestamp', [sa.DateTime(timezone=True), pg.TIMESTAMP(timezone=True, precision=6)])
def test_accepts_legitimate_postgres_representations(legacy, default, timestamp):
    connection, _, columns = legacy
    columns['id']['default'] = default
    columns['created_at']['type'] = timestamp
    columns['created_at']['default'] = ' CURRENT_TIMESTAMP(6) '
    verify_legacy_schema(connection)


@pytest.mark.parametrize('name, kind', [
    ('id', sa.BigInteger()), ('id', pg.BIGINT()), ('id', sa.SmallInteger()),
    ('id', sa.Numeric(10, 0)), ('company_name', sa.CHAR(150)),
    ('company_name', pg.VARCHAR(151)), ('company_name', pg.TEXT()),
    ('job_description', pg.VARCHAR()), ('application_date', pg.TIMESTAMP()),
    ('created_at', pg.TIMESTAMP(timezone=False)),
    ('created_at', pg.TIMESTAMP(timezone=True, precision=3)),
])
def test_rejects_incompatible_types(legacy, name, kind):
    connection, _, columns = legacy
    columns[name]['type'] = kind
    with pytest.raises(RuntimeError, match='baseline type/nullability'):
        verify_legacy_schema(connection)


@pytest.mark.parametrize('name, attribute, value', [
    ('company_name', 'nullable', True), ('created_at', 'nullable', True),
    ('company_name', 'computed', {'sqltext': "'generated'"}),
    ('id', 'identity', {'always': True}), ('id', 'autoincrement', False),
    ('status', 'default', "'saved'::character varying"),
    ('created_at', 'default', 'clock_timestamp()'),
    ('id', 'default', "nextval('job_applications_id_seq'::regclass) + 1"),
    ('id', 'default', "nextval('other.job_applications_id_seq'::regclass)"),
    ('id', 'default', "nextval('other_sequence'::regclass)"),
])
def test_rejects_attribute_and_default_drift(legacy, name, attribute, value):
    connection, _, columns = legacy
    columns[name][attribute] = value
    with pytest.raises(RuntimeError):
        verify_legacy_schema(connection)


@pytest.mark.parametrize('attribute', ['get_view_names', 'get_materialized_view_names'])
def test_rejects_unexpected_views(legacy, attribute):
    connection, inspector, _ = legacy
    getattr(inspector, attribute).return_value = ['unexpected']
    with pytest.raises(RuntimeError):
        verify_legacy_schema(connection)


@pytest.mark.parametrize('results', [[1], [0, 1]])
def test_rejects_triggers_or_nonbaseline_table_options(legacy, results):
    connection, _, _ = legacy
    connection.scalar.side_effect = results
    with pytest.raises(RuntimeError):
        verify_legacy_schema(connection)
