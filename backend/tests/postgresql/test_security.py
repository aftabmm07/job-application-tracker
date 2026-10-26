"""Opt-in tests: NEVER run without separately approved disposable infrastructure."""
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event, Lock
from time import monotonic, sleep

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.orm import Session

from app.main import app
from app.models import AuthSession, JobApplication, User
from app.passwords import hash_password
from app.session_service import SessionService, SessionStorageError, token_digest

PASSWORD = 'isolated integration password'
ORIGIN = 'http://localhost:5173'


def client():
    return TestClient(app, headers={'Origin': ORIGIN})


def bootstrap(browser):
    response = browser.get('/auth/csrf')
    assert response.status_code == 200
    browser.headers['X-CSRF-Token'] = response.json()['csrf_token']
    browser.headers['Content-Type'] = 'application/json'


def seed_user(engine, email='alice@example.com'):
    with Session(engine) as db:
        user = User(email=email, password_hash=hash_password(PASSWORD), is_active=True,
                    email_verified_at=datetime.now(UTC))
        db.add(user)
        db.flush()
        identifier = user.id
        db.commit()
        return identifier


def sign_in(browser, email='alice@example.com'):
    bootstrap(browser)
    assert browser.post('/auth/login', json={'email': email, 'password': PASSWORD}).status_code == 200
    bootstrap(browser)  # CSRF changes when the session cookie rotates.


def parallel(first, second):
    with ThreadPoolExecutor(max_workers=2) as workers:
        a, b = workers.submit(first), workers.submit(second)
        return a.result(timeout=15), b.result(timeout=15)


def wait_for_postgres_lock(observer):
    """Observe actual server blocking, not just a client-side execution callback."""
    deadline = monotonic() + 1.5  # Below the service's two-second lock timeout.
    while monotonic() < deadline:
        waiting = observer.scalar(text("""SELECT count(*) FROM pg_stat_activity
                WHERE application_name = 'jobtracker-isolated-tests'
                  AND pid <> pg_backend_pid() AND wait_event_type = 'Lock'
                  AND query LIKE '%FOR UPDATE%'"""))
        if waiting:
            return
        sleep(0.01)
    pytest.fail('PostgreSQL waiter did not acquire a bounded observable lock wait')


def test_duplicate_registration_race(api):
    engine, settings = api
    rendezvous = Barrier(2, timeout=5)
    counter_lock = Lock()
    checked = 0

    def after_select(connection, cursor, statement, parameters, context, executemany):
        nonlocal checked
        if ' '.join(statement.split()).startswith('SELECT users.id FROM users WHERE users.email ='):
            with counter_lock:
                checked += 1
                initial_check = checked <= 2
            if initial_check:
                rendezvous.wait()  # Both existence checks finish before either INSERT.

    with client() as alice, client() as bob:
        bootstrap(alice)
        bootstrap(bob)
        event.listen(engine, 'after_cursor_execute', after_select)
        try:
            responses = parallel(
                lambda: alice.post('/auth/register', json={'email': ' Race@Example.com ', 'password': PASSWORD}),
                lambda: bob.post('/auth/register', json={'email': 'race@example.com', 'password': PASSWORD}),
            )
        finally:
            event.remove(engine, 'after_cursor_execute', after_select)
        assert [response.status_code for response in responses] == [202, 202]
        assert responses[0].json() == responses[1].json()
    with Session(engine) as db:
        users = db.scalars(select(User)).all()
        assert len(users) == 1
        assert users[0].email == 'race@example.com'
        assert not users[0].is_active and users[0].email_verified_at is None
        assert db.scalar(select(func.count()).select_from(AuthSession).where(AuthSession.user_id.is_not(None))) == 0


def test_parallel_login_consumes_anonymous_session_once(api, monkeypatch):
    engine, settings = api
    user_id = seed_user(engine)
    rendezvous = Barrier(2, timeout=5)
    original = SessionService.rotate_for_login

    def synchronized(service, raw, identifier):
        rendezvous.wait()  # Both requests passed CSRF and real password verification.
        return original(service, raw, identifier)

    monkeypatch.setattr(SessionService, 'rotate_for_login', synchronized)
    with client() as alice, client() as bob:
        bootstrap(alice)
        raw = alice.cookies.get(settings.session_cookie_name)
        bob.cookies.set(settings.session_cookie_name, raw)
        bob.headers.update({'X-CSRF-Token': alice.headers['X-CSRF-Token'], 'Content-Type': 'application/json'})
        responses = parallel(
            lambda: alice.post('/auth/login', json={'email': 'alice@example.com', 'password': PASSWORD}),
            lambda: bob.post('/auth/login', json={'email': 'alice@example.com', 'password': PASSWORD}),
        )
        assert sorted(response.status_code for response in responses) == [200, 401]
        winner = next(response for response in responses if response.status_code == 200)
        loser = next(response for response in responses if response.status_code == 401)
        has_httponly = 'httponly' in winner.headers.get('set-cookie', '').lower()
        assert has_httponly, 'Successful login must issue an HttpOnly cookie'
        loser_issued_cookie = 'set-cookie' in loser.headers
        assert not loser_issued_cookie, 'Failed login must not issue a session cookie'
    with Session(engine) as db:
        assert db.scalar(select(AuthSession).where(AuthSession.token_hash == token_digest(raw))) is None
        assert db.scalar(select(func.count()).select_from(AuthSession).where(AuthSession.user_id == user_id)) == 1


@pytest.mark.parametrize('first', ['logout', 'renew'])
def test_logout_versus_renewal(api, first):
    engine, settings = api
    seed_user(engine)
    waiting = Event()

    def before_lock(connection, cursor, statement, parameters, context, executemany):
        if 'FOR UPDATE' in statement:
            waiting.set()

    with client() as browser:
        sign_in(browser)
        raw = browser.cookies.get(settings.session_cookie_name)
        with engine.connect().execution_options(isolation_level='AUTOCOMMIT') as observer, Session(engine) as holder, ThreadPoolExecutor(max_workers=1) as workers:
            service = SessionService(holder, settings)
            assert (service.revoke(raw) if first == 'logout' else service.renew(raw))
            event.listen(engine, 'before_cursor_execute', before_lock)
            try:
                future = workers.submit(browser.get, '/auth/me') if first == 'logout' else workers.submit(
                    browser.post, '/auth/logout', json={})
                assert waiting.wait(5)
                wait_for_postgres_lock(observer)
            finally:
                holder.commit()
                event.remove(engine, 'before_cursor_execute', before_lock)
            assert future.result(timeout=10).status_code == (401 if first == 'logout' else 204)
        assert browser.get('/auth/me').status_code == 401
    with Session(engine) as db:
        assert db.scalar(select(AuthSession).where(AuthSession.token_hash == token_digest(raw))) is None


@pytest.mark.parametrize('operation', ['renew', 'rotate', 'revoke'])
@pytest.mark.parametrize('boundary', ['absolute', 'idle'])
def test_expiration_rechecked_after_postgresql_lock_wait(api, operation, boundary):
    engine, settings = api
    user_id = seed_user(engine)
    now = datetime.now(UTC)
    with Session(engine) as db:
        issued = SessionService(db, settings, clock=lambda: now).create()
        if boundary == 'absolute':
            db.get(AuthSession, issued.session_id).idle_expires_at = issued.expires_at
        db.commit()
    # Inject time, not SQL locking: PostgreSQL still performs the actual lock wait.
    deadline = issued.expires_at if boundary == 'absolute' else now + timedelta(seconds=settings.session_idle_timeout_seconds)
    clock = [now + timedelta(seconds=1)]
    waiting = Event()

    def before_lock(connection, cursor, statement, parameters, context, executemany):
        if 'FOR UPDATE' in statement:
            waiting.set()

    def waiter():
        with Session(engine) as db:
            service = SessionService(db, settings, clock=lambda: clock[0])
            if operation == 'rotate':
                from app.session_service import InvalidSessionError
                with pytest.raises(InvalidSessionError):
                    service.rotate_for_login(issued.raw_token, user_id)
                db.rollback()
                return None
            result = getattr(service, operation)(issued.raw_token)
            db.commit()
            return result

    with engine.connect().execution_options(isolation_level='AUTOCOMMIT') as observer, Session(engine) as holder, ThreadPoolExecutor(max_workers=1) as workers:
        original = holder.scalar(select(AuthSession).where(AuthSession.id == issued.session_id).with_for_update())
        initial_activity = original.last_activity_at
        event.listen(engine, 'before_cursor_execute', before_lock)
        try:
            future = workers.submit(waiter)
            assert waiting.wait(5)
            wait_for_postgres_lock(observer)
            clock[0] = deadline  # Exactly the expiry boundary while row is held.
        finally:
            holder.commit()
            event.remove(engine, 'before_cursor_execute', before_lock)
        result = future.result(timeout=10)
        assert result is None or result is False
    with Session(engine) as db:
        record = db.get(AuthSession, issued.session_id)
        assert record.last_activity_at == initial_activity
        assert record.expires_at == issued.expires_at
        assert db.scalar(select(func.count()).select_from(AuthSession)) == 1


def test_lock_timeout_rolls_back(api):
    engine, settings = api
    with Session(engine) as db:
        issued = SessionService(db, settings).create()
        db.commit()

    def waiter():
        with Session(engine) as db:
            with pytest.raises(SessionStorageError, match='Session storage is unavailable'):
                SessionService(db, settings).renew(issued.raw_token)
            # The service rolled back; this connection remains usable.
            assert db.scalar(select(func.count()).select_from(AuthSession)) == 1

    with engine.connect().execution_options(isolation_level='AUTOCOMMIT') as observer, Session(engine) as holder, ThreadPoolExecutor(max_workers=1) as workers:
        holder.scalar(select(AuthSession).where(AuthSession.id == issued.session_id).with_for_update())
        future = workers.submit(waiter)
        try:
            future.result(timeout=10)  # Actual server-side 2s lock timeout.
        finally:
            holder.rollback()


def test_concurrent_cleanup_skips_locks_and_preserves_active_sessions(api):
    engine, settings = api
    now = datetime.now(UTC)
    user_id = seed_user(engine)
    with Session(engine) as db:
        expired = [SessionService(db, settings, clock=lambda: now - timedelta(days=2)).create(
            user_id if index % 2 else None) for index in range(5)]
        active = SessionService(db, settings, clock=lambda: now).create(user_id)
        db.add(JobApplication(company_name='Preserved', job_title='Active record', user_id=user_id))
        db.commit()
    rendezvous = Barrier(2, timeout=5)

    def cleanup():
        with Session(engine) as db:
            removed = SessionService(db, settings, clock=lambda: now).cleanup_expired(batch_size=2)
            rendezvous.wait()  # Both batches remain locked until both selected/deleted.
            db.commit()
            return removed

    with Session(engine) as holder:
        holder.scalar(select(AuthSession).where(AuthSession.id == expired[0].session_id).with_for_update())
        try:
            assert parallel(cleanup, cleanup) == (2, 2)
        finally:
            holder.rollback()
    with Session(engine) as db:
        assert set(db.scalars(select(AuthSession.id))) == {expired[0].session_id, active.session_id}
        service = SessionService(db, settings, clock=lambda: now)
        assert service.cleanup_expired(batch_size=2) == 1
        db.commit()
        assert service.cleanup_expired(batch_size=2) == 0
        db.commit()
        assert db.get(AuthSession, active.session_id) is not None
        assert db.get(User, user_id) is not None
        assert db.scalar(select(func.count()).select_from(JobApplication)) == 1


def test_ownership_isolation_with_independent_clients(api):
    engine, settings = api
    seed_user(engine)
    seed_user(engine, 'bob@example.com')
    with Session(engine) as db:
        legacy = JobApplication(company_name='Unowned', job_title='Quarantined', status='saved')
        db.add(legacy)
        db.flush()
        legacy_id = legacy.id
        db.commit()
    with client() as alice, client() as bob:
        sign_in(alice)
        sign_in(bob, 'bob@example.com')
        responses = parallel(
            lambda: alice.post('/applications', json={'company_name': 'Alice', 'job_title': 'Engineer'}),
            lambda: bob.post('/applications', json={'company_name': 'Bob', 'job_title': 'Analyst'}),
        )
        assert all(response.status_code == 201 for response in responses)
        alice_id, bob_id = [response.json()['id'] for response in responses]
        for browser, own, foreign in ((alice, alice_id, bob_id), (bob, bob_id, alice_id)):
            assert [row['id'] for row in browser.get('/applications').json()] == [own]
            for identifier in (foreign, legacy_id, 2147483647):
                for method in ('GET', 'PATCH', 'DELETE'):
                    response = browser.request(method, f'/applications/{identifier}',
                                               json={'status': 'offer'} if method == 'PATCH' else None)
                    assert response.status_code == 404
                    assert response.json() == {'detail': 'Job application not found'}
            assert browser.patch(f'/applications/{own}', json={'status': 'offer'}).status_code == 200
            assert browser.delete(f'/applications/{own}').status_code == 204
    with Session(engine) as db:
        legacy = db.get(JobApplication, legacy_id)
        assert legacy.user_id is None and legacy.company_name == 'Unowned'


def test_cleanup_does_not_delete_concurrently_renewed_session(api):
    engine, settings = api
    now = datetime.now(UTC)
    with Session(engine) as db:
        issued = SessionService(db, settings, clock=lambda: now).create()
        db.commit()
    with engine.connect().execution_options(isolation_level='AUTOCOMMIT') as observer, Session(engine) as holder, ThreadPoolExecutor(max_workers=1) as workers:
        assert SessionService(holder, settings, clock=lambda: now + timedelta(seconds=59)).renew(issued.raw_token)

        def cleanup():
            with Session(engine) as db:
                removed = SessionService(db, settings, clock=lambda: now + timedelta(seconds=60)).cleanup_expired()
                db.commit()
                return removed

        try:
            # The old committed idle expiry matches, but SKIP LOCKED must skip it.
            assert workers.submit(cleanup).result(timeout=5) == 0
        finally:
            holder.commit()
    with Session(engine) as db:
        assert SessionService(db, settings, clock=lambda: now + timedelta(seconds=60)).lookup(issued.raw_token)
        assert db.scalar(select(func.count()).select_from(AuthSession)) == 1
