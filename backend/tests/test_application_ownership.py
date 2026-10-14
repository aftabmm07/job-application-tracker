from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.auth_config import AuthSettings, get_auth_settings
from app.auth_limits import AuthRateLimiter, get_auth_limiter
from app.main import app, get_db
from app.models import AuthSession, Base, JobApplication, User
from app.passwords import hash_password
from app.session_service import SessionService, token_digest

PASSWORD = 'ownership test account password'
ORIGIN = 'http://localhost:5173'
PAYLOAD = {'company_name': 'Owned company', 'job_title': 'Engineer', 'status': 'applied'}
ROUTES = [('GET', '/applications'), ('GET', '/applications/1'), ('POST', '/applications'),
          ('PATCH', '/applications/1'), ('DELETE', '/applications/1')]


@pytest.fixture(scope='module')
def password_hash():
    return hash_password(PASSWORD)


@pytest.fixture
def ctx(password_hash):
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    settings = AuthSettings.from_environment({})
    limiter = AuthRateLimiter()
    with Session(engine, autoflush=False) as db, ExitStack() as stack:
        users = [User(email=f'{name}@example.com', password_hash=password_hash, is_active=True,
                      email_verified_at=datetime.now(UTC)) for name in ('alice', 'bob')]
        db.add_all(users)
        db.commit()
        ids = [user.id for user in users]
        def override_db():
            yield db
        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_auth_settings] = lambda: settings
        app.dependency_overrides[get_auth_limiter] = lambda: limiter
        clients = []
        try:
            for name in ('alice', 'bob'):
                client = stack.enter_context(TestClient(app))
                origin = {'Origin': ORIGIN}
                csrf = client.get('/auth/csrf', headers=origin).json()['csrf_token']
                response = client.post('/auth/login', json={'email': f'{name}@example.com', 'password': PASSWORD},
                                       headers=origin | {'X-CSRF-Token': csrf})
                assert response.status_code == 200
                csrf = client.get('/auth/csrf', headers=origin).json()['csrf_token']
                client.headers.update(origin | {'X-CSRF-Token': csrf, 'Content-Type': 'application/json'})
                clients.append(client)
            yield SimpleNamespace(a=clients[0], b=clients[1], a_id=ids[0], b_id=ids[1],
                                  db=db, settings=settings, engine=engine)
        finally:
            for dependency in (get_db, get_auth_settings, get_auth_limiter):
                app.dependency_overrides.pop(dependency, None)
    engine.dispose()


def mutation(client, method, path, **kwargs):
    return client.request(method, path, json=PAYLOAD if method == 'POST' else {'status': 'offer'} if method == 'PATCH' else None, **kwargs)


def create(client, **fields):
    response = client.post('/applications', json=PAYLOAD | fields)
    assert response.status_code == 201
    return response.json()


def legacy(ctx):
    row = JobApplication(company_name='Private legacy', job_title='Legacy engineer', status='saved')
    ctx.db.add(row)
    ctx.db.commit()
    return row.id


@pytest.mark.parametrize('method,path', ROUTES)
@pytest.mark.parametrize('anonymous_cookie', [False, True])
def test_anonymous_access_denied(ctx, method, path, anonymous_cookie):
    with TestClient(app) as anonymous:
        if anonymous_cookie:
            csrf = anonymous.get('/auth/csrf', headers={'Origin': ORIGIN}).json()['csrf_token']
            anonymous.headers['X-CSRF-Token'] = csrf
        anonymous.headers.update({'Origin': ORIGIN, 'Content-Type': 'application/json'})
        response = mutation(anonymous, method, path)
    assert response.status_code == 401
    assert response.json() == {'detail': 'Authentication required'}
    assert response.headers['cache-control'] == 'no-store'


def test_owner_creation_shape_and_crud(ctx):
    result = create(ctx.a)
    record = ctx.db.get(JobApplication, result['id'])
    assert record.user_id == ctx.a_id
    assert set(result) == {'id', 'created_at', 'company_name', 'job_title', 'location',
                           'status', 'application_date', 'job_description'}
    assert ctx.a.get('/applications').json() == [result]
    assert ctx.a.get(f"/applications/{result['id']}").json() == result
    updated = ctx.a.patch(f"/applications/{result['id']}", json={'status': 'interview', 'location': 'Berlin'})
    assert updated.status_code == 200 and updated.json()['status'] == 'interview'
    assert ctx.a.delete(f"/applications/{result['id']}").status_code == 204
    assert ctx.a.get(f"/applications/{result['id']}").status_code == 404


def test_lists_and_client_analytics_use_only_owned_rows(ctx):
    a1 = create(ctx.a, status='saved')
    a2 = create(ctx.a, status='offer')
    b1 = create(ctx.b, company_name='Bob company', status='interview')
    old = legacy(ctx)
    a_rows = ctx.a.get('/applications').json()
    b_rows = ctx.b.get('/applications').json()
    assert [row['id'] for row in a_rows] == [a2['id'], a1['id']]
    assert [row['id'] for row in b_rows] == [b1['id']]
    # The frontend calculates analytics from this same scoped list (no analytics API).
    assert sum(row['status'] == 'offer' for row in a_rows) == 1
    assert sum(row['status'] == 'offer' for row in b_rows) == 0
    assert ctx.db.get(JobApplication, old).user_id is None


@pytest.mark.parametrize('method', ['GET', 'PATCH', 'DELETE'])
def test_foreign_legacy_and_missing_ids_are_indistinguishable(ctx, method):
    a = create(ctx.a)
    old = legacy(ctx)
    original = ctx.db.get(JobApplication, old)
    before = (original.company_name, original.job_title, original.status, original.user_id)
    responses = [mutation(ctx.b, method, f'/applications/{application_id}') for application_id in (a['id'], old, 999999)]
    assert all(response.status_code == 404 for response in responses)
    assert all(response.json() == {'detail': 'Job application not found'} for response in responses)
    assert all(response.headers['cache-control'] == 'no-store' for response in responses)
    ctx.db.refresh(original)
    assert (original.company_name, original.job_title, original.status, original.user_id) == before
    assert ctx.a.get(f"/applications/{a['id']}").json()['status'] == 'applied'


@pytest.mark.parametrize('value', [None, 1, 999])
@pytest.mark.parametrize('method', ['POST', 'PATCH'])
def test_client_cannot_set_or_transfer_ownership(ctx, value, method):
    result = create(ctx.a)
    response = ctx.a.request(method, '/applications' if method == 'POST' else f"/applications/{result['id']}",
                             json=(PAYLOAD if method == 'POST' else {'status': 'offer'}) | {'user_id': value})
    assert response.status_code == 422
    record = ctx.db.get(JobApplication, result['id'])
    ctx.db.refresh(record)
    assert record.user_id == ctx.a_id and record.status == 'applied'
    assert len(ctx.a.get('/applications').json()) == 1


@pytest.mark.parametrize('method', ['POST', 'PATCH', 'DELETE'])
@pytest.mark.parametrize('failure', ['missing', 'invalid', 'cross-session', 'origin', 'no-origin', 'media'])
def test_mutations_enforce_csrf_origin_and_json(ctx, method, failure):
    result = create(ctx.a)
    request_headers = dict(ctx.a.headers)
    if failure == 'missing':
        request_headers.pop('x-csrf-token', None)
    elif failure == 'invalid':
        request_headers['x-csrf-token'] = '0' * 64
    elif failure == 'cross-session':
        request_headers['x-csrf-token'] = ctx.b.headers['x-csrf-token']
    elif failure == 'origin':
        request_headers['origin'] = 'http://evil.example'
    elif failure == 'no-origin':
        request_headers.pop('origin', None)
    else:
        request_headers['content-type'] = 'text/plain'
    # Build a separate Request to avoid TestClient merging removed default headers.
    import httpx
    response = ctx.a.send(httpx.Request(method, 'http://testserver' + ('/applications' if method == 'POST' else f"/applications/{result['id']}"),
        headers=request_headers | {'cookie': f"session={ctx.a.cookies.get('session')}"},
        json=PAYLOAD if method == 'POST' else {'status': 'offer'} if method == 'PATCH' else None))
    assert response.status_code == (415 if failure == 'media' else 403)
    assert response.headers['cache-control'] == 'no-store'
    record = ctx.db.get(JobApplication, result['id'])
    ctx.db.refresh(record)
    assert record.status == 'applied' and record.user_id == ctx.a_id
    assert len(ctx.a.get('/applications').json()) == 1


def test_trusted_referer_fallback_for_mutation(ctx):
    ctx.a.headers.pop('origin')
    ctx.a.headers['Referer'] = ORIGIN + '/applications'
    assert ctx.a.post('/applications', json=PAYLOAD).status_code == 201


@pytest.mark.parametrize('reason', ['absolute', 'idle', 'revoked', 'inactive'])
@pytest.mark.parametrize('method,path', ROUTES)
def test_invalid_sessions_cannot_access_any_route(ctx, reason, method, path):
    create(ctx.a)
    record = ctx.db.scalar(select(AuthSession).where(AuthSession.token_hash == token_digest(ctx.a.cookies.get('session'))))
    now = datetime.now(UTC)
    if reason == 'revoked':
        ctx.db.delete(record)
    elif reason == 'inactive':
        ctx.db.get(User, ctx.a_id).is_active = False
    else:
        record.created_at = now - timedelta(days=2)
        record.last_activity_at = record.created_at
        if reason == 'absolute':
            record.expires_at = now - timedelta(days=1)
            record.idle_expires_at = record.expires_at
        else:
            record.expires_at = now + timedelta(hours=1)
            record.idle_expires_at = now - timedelta(minutes=1)
    ctx.db.commit()
    response = mutation(ctx.a, method, path)
    assert response.status_code == 401
    assert response.headers['cache-control'] == 'no-store'


@pytest.mark.parametrize('method', ['POST', 'PATCH', 'DELETE'])
def test_commit_failure_rolls_back_application_and_activity(ctx, method):
    result = create(ctx.a)
    original = ctx.db.get(JobApplication, result['id'])
    record = ctx.db.scalar(select(AuthSession).where(AuthSession.user_id == ctx.a_id))
    before_activity = record.last_activity_at
    with patch.object(ctx.db, 'commit', side_effect=SQLAlchemyError('private driver details')):
        response = mutation(ctx.a, method, '/applications' if method == 'POST' else f"/applications/{result['id']}")
    assert response.status_code == 503
    assert response.json() == {'detail': 'Database operation failed'}
    assert 'private driver details' not in response.text
    ctx.db.refresh(original)
    ctx.db.refresh(record)
    assert original.status == 'applied' and record.last_activity_at == before_activity
    assert len(ctx.a.get('/applications').json()) == 1


@pytest.mark.parametrize('method', ['POST', 'PATCH', 'DELETE'])
def test_session_revocation_before_final_renewal_rolls_back_mutation(ctx, method):
    result = create(ctx.a)
    with patch.object(SessionService, 'renew', return_value=None):
        response = mutation(ctx.a, method, '/applications' if method == 'POST' else f"/applications/{result['id']}")
    assert response.status_code == 401
    assert ctx.a.get(f"/applications/{result['id']}").json()['status'] == 'applied'
    assert len(ctx.a.get('/applications').json()) == 1


def test_empty_patch_and_nullable_fields_remain_supported(ctx):
    result = create(ctx.a, location='Berlin', job_description='Description', application_date='2026-01-31')
    unchanged = ctx.a.patch(f"/applications/{result['id']}", json={})
    assert unchanged.status_code == 200 and unchanged.json() == result
    changed = ctx.a.patch(f"/applications/{result['id']}", json={'location': None, 'job_description': None, 'application_date': None})
    assert changed.status_code == 200
    assert changed.json()['location'] is None and changed.json()['application_date'] is None


def test_owner_predicates_are_in_update_and_delete_sql(ctx):
    result = create(ctx.a)
    statements = []
    def capture(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith(('UPDATE JOB_APPLICATIONS', 'DELETE FROM JOB_APPLICATIONS')):
            statements.append(statement)
    event.listen(ctx.engine, 'before_cursor_execute', capture)
    try:
        assert ctx.a.patch(f"/applications/{result['id']}", json={'status': 'offer'}).status_code == 200
        assert ctx.a.delete(f"/applications/{result['id']}").status_code == 204
    finally:
        event.remove(ctx.engine, 'before_cursor_execute', capture)
    assert len(statements) == 2
    for statement in statements:
        where = statement.upper().split('WHERE', 1)[1].split('RETURNING', 1)[0]
        assert 'JOB_APPLICATIONS.ID' in where and 'JOB_APPLICATIONS.USER_ID' in where


@pytest.mark.parametrize('kind', ['list', 'detail'])
def test_read_errors_are_sanitized_and_rolled_back(ctx, kind):
    result = create(ctx.a)
    if kind == 'list':
        target = 'scalars'
        effect = SQLAlchemyError('private database diagnostic')
    else:
        from sqlalchemy.sql import Select
        target = 'scalar'
        original = ctx.db.scalar
        def effect(statement, *args, **kwargs):
            if isinstance(statement, Select) and statement.column_descriptions[0].get('entity') is JobApplication:
                raise SQLAlchemyError('private database diagnostic')
            return original(statement, *args, **kwargs)
    with patch.object(ctx.db, target, side_effect=effect), patch.object(ctx.db, 'rollback', wraps=ctx.db.rollback) as rollback:
        response = ctx.a.get('/applications' if kind == 'list' else f"/applications/{result['id']}")
    assert response.status_code == 503
    assert response.json() == {'detail': 'Database operation failed'}
    assert rollback.called
    assert response.headers['cache-control'] == 'no-store'


def test_read_queries_include_owner_predicate(ctx):
    result = create(ctx.a)
    statements = []
    def capture(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith('SELECT') and 'FROM job_applications' in statement:
            statements.append(statement)
    event.listen(ctx.engine, 'before_cursor_execute', capture)
    try:
        assert ctx.a.get('/applications').status_code == 200
        assert ctx.a.get(f"/applications/{result['id']}").status_code == 200
    finally:
        event.remove(ctx.engine, 'before_cursor_execute', capture)
    assert len(statements) == 2
    assert all('JOB_APPLICATIONS.USER_ID' in sql.upper().split('WHERE', 1)[1] for sql in statements)


def test_failed_security_checks_do_not_renew_activity(ctx):
    result = create(ctx.a)
    record = ctx.db.scalar(select(AuthSession).where(AuthSession.user_id == ctx.a_id))
    before = record.last_activity_at
    response = ctx.a.patch(f"/applications/{result['id']}", json={'status': 'offer'}, headers={'X-CSRF-Token': '0' * 64})
    assert response.status_code == 403
    ctx.db.refresh(record)
    assert record.last_activity_at == before
