"""Real independent SQLite writers; PostgreSQL scheduling still needs rehearsal."""
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import UTC, datetime, timedelta
from threading import Event, get_ident

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from app.auth_config import AuthSettings
from app.models import AuthSession, Base
from app.session_service import SessionService

NOW = datetime(2026, 10, 15, 12, tzinfo=UTC)


@pytest.mark.parametrize('scenario', ['revoke-renew', 'renew-revoke', 'expired-wait', 'renew-cleanup'])
def test_serialized_session_operations(tmp_path, scenario):
    engine = create_engine(f"sqlite:///{tmp_path / 'sessions.sqlite'}")
    Base.metadata.create_all(engine)
    settings = AuthSettings.from_environment({'SESSION_IDLE_TIMEOUT_SECONDS': '600'})
    with Session(engine) as setup:
        issued = SessionService(setup, settings, clock=lambda: NOW).create()
        setup.commit()

    # The second writer signals immediately before trying its write reservation.
    # It cannot sample its authorization time until the holder commits.
    waiting = Event()
    main_thread = get_ident()

    def before_execute(connection, cursor, statement, parameters, context, executemany):
        if get_ident() != main_thread and statement.lstrip().upper().startswith('UPDATE SESSIONS'):
            waiting.set()

    event.listen(engine, 'before_cursor_execute', before_execute)
    second_time = [NOW + timedelta(seconds=10)]

    def second_writer():
        with Session(engine) as db:
            current = SessionService(db, settings, clock=lambda: second_time[0])
            if scenario == 'renew-revoke':
                result = current.revoke(issued.raw_token)
            elif scenario == 'renew-cleanup':
                result = current.cleanup_expired()
            else:
                result = current.renew(issued.raw_token)
            db.commit()
            return result

    try:
        with Session(engine) as holder, ThreadPoolExecutor(max_workers=1) as workers:
            current = SessionService(holder, settings, clock=lambda: NOW + timedelta(seconds=5))
            if scenario == 'revoke-renew':
                assert current.revoke(issued.raw_token)
            elif scenario in {'renew-revoke', 'renew-cleanup'}:
                assert current.renew(issued.raw_token) is not None
            else:
                current._prepare_write()  # Hold only the writer reservation.
            future = workers.submit(second_writer)
            try:
                assert waiting.wait(5)
                with pytest.raises(TimeoutError):
                    future.result(timeout=0.05)
                if scenario == 'expired-wait':
                    second_time[0] = issued.expires_at
                elif scenario == 'renew-cleanup':
                    second_time[0] = NOW + timedelta(seconds=600)
            finally:
                holder.commit()
            result = future.result(timeout=5)
            if scenario in {'revoke-renew', 'expired-wait'}:
                assert result is None
            elif scenario == 'renew-revoke':
                assert result is True
            else:
                assert result == 0
        with Session(engine) as check:
            record = check.scalar(select(AuthSession))
            if scenario in {'revoke-renew', 'renew-revoke'}:
                assert record is None
            else:
                assert record.last_activity_at == NOW + timedelta(seconds=5 if scenario == 'renew-cleanup' else 0)
    finally:
        event.remove(engine, 'before_cursor_execute', before_execute)
        engine.dispose()
