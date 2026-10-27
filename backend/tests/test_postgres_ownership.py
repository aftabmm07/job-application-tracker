"""Opt-in CI-only PostgreSQL API check; NEVER targets a developer database.

This writes disposable CI rows inside an outer transaction and rolls them back.
Run only on the freshly migrated jobtracker_auth_ci service. Local execution of
this test with the opt-in flag requires explicit database-changing approval.
"""
from datetime import UTC, datetime
import os

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.auth_config import AuthSettings, get_auth_settings
from app.auth_limits import AuthRateLimiter, get_auth_limiter
from app.main import app, get_db
from app.migration_safety import check_target, migration_url
from app.models import AuthSession, JobApplication, User
from app.passwords import hash_password


@pytest.mark.skipif(os.getenv('RUN_POSTGRES_OWNERSHIP_TESTS') != '1', reason='PostgreSQL ownership check requires explicit disposable-CI opt-in')
def test_postgres_api_ownership_with_transaction_rollback():
    # Check BEFORE connecting; never use the default app engine or a restore DB.
    if os.getenv('MIGRATION_DATABASE_NAME') != 'jobtracker_auth_ci':
        pytest.fail('PostgreSQL ownership checks only allow jobtracker_auth_ci')
    engine = create_engine(migration_url())
    settings = AuthSettings.from_environment({})
    limiter = AuthRateLimiter()
    try:
        with engine.connect() as connection:
            outer = connection.begin()
            try:
                check_target(connection, 'jobtracker_auth_ci')
                for model in (User, AuthSession, JobApplication):
                    assert connection.scalar(select(func.count()).select_from(model)) == 0, 'CI database must start empty'
                with Session(bind=connection, join_transaction_mode='create_savepoint', autoflush=False) as db:
                    encoded = hash_password('postgres ownership test password')
                    db.add_all([User(email=f'{name}@example.com', password_hash=encoded, is_active=True,
                                     email_verified_at=datetime.now(UTC)) for name in ('alice', 'bob')])
                    legacy = JobApplication(company_name='Legacy', job_title='Original', status='saved')
                    db.add(legacy)
                    db.commit()
                    legacy_id = legacy.id
                    def override_db():
                        yield db
                    app.dependency_overrides[get_db] = override_db
                    app.dependency_overrides[get_auth_settings] = lambda: settings
                    app.dependency_overrides[get_auth_limiter] = lambda: limiter
                    try:
                        with TestClient(app) as alice, TestClient(app) as bob:
                            for client, name in ((alice, 'alice'), (bob, 'bob')):
                                client.headers['Origin'] = 'http://localhost:5173'
                                csrf = client.get('/auth/csrf').json()['csrf_token']
                                response = client.post('/auth/login', json={'email': f'{name}@example.com',
                                    'password': 'postgres ownership test password'}, headers={'X-CSRF-Token': csrf})
                                assert response.status_code == 200
                                client.headers['X-CSRF-Token'] = client.get('/auth/csrf').json()['csrf_token']
                                client.headers['Content-Type'] = 'application/json'
                            response = alice.post('/applications', json={'company_name': 'Owned', 'job_title': 'Engineer'})
                            assert response.status_code == 201
                            app_id = response.json()['id']
                            assert bob.get('/applications').json() == []
                            for guessed in (app_id, legacy_id, 999999):
                                for method in ('GET', 'PATCH', 'DELETE'):
                                    response = bob.request(method, f'/applications/{guessed}',
                                                           json={'status': 'offer'} if method == 'PATCH' else None)
                                    assert response.status_code == 404
                                    assert response.json() == {'detail': 'Job application not found'}
                            assert alice.patch(f'/applications/{app_id}', json={'status': 'offer'}).status_code == 200
                            assert alice.get('/applications').json()[0]['status'] == 'offer'
                            assert alice.delete(f'/applications/{app_id}').status_code == 204
                            assert db.get(JobApplication, legacy_id).user_id is None
                            assert db.get(JobApplication, legacy_id).company_name == 'Legacy'
                    finally:
                        for dependency in (get_db, get_auth_settings, get_auth_limiter):
                            app.dependency_overrides.pop(dependency, None)
            finally:
                outer.rollback()
        with engine.connect() as connection:
            for model in (User, AuthSession, JobApplication):
                assert connection.scalar(select(func.count()).select_from(model)) == 0
    finally:
        engine.dispose()
