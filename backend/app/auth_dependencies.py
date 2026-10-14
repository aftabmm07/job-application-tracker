"""Reusable read-only authentication dependency; CRUD adoption is a later task."""
from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.auth_config import AuthSettings, get_auth_settings
from app.dependencies import get_db
from app.models import User
from app.session_http import no_store
from app.session_service import SessionService, SessionStorageError


def auth_error(status, message):
    raise HTTPException(status, message, headers={"Cache-Control": "no-store"})


def get_auth_db(request: Request, db: Session = Depends(get_db)):
    request.state.auth_db = db
    try:
        yield db
    except Exception:
        try:
            db.rollback()
        except SQLAlchemyError:
            pass
        raise


def get_current_user(request: Request, response: Response,
                     db: Session = Depends(get_auth_db), settings: AuthSettings = Depends(get_auth_settings)) -> User:
    no_store(response)
    try:
        record = SessionService(db, settings).lookup(request.cookies.get(settings.session_cookie_name))
        if record is None or record.user_id is None:
            auth_error(401, "Authentication required")
        user = db.scalar(select(User).where(User.id == record.user_id, User.is_active.is_(True),
                                            User.email_verified_at.is_not(None)).execution_options(populate_existing=True))
        if user is None:
            auth_error(401, "Authentication required")
    except (SQLAlchemyError, SessionStorageError):
        try:
            db.rollback()
        except SQLAlchemyError:
            pass
        auth_error(503, "Authentication service unavailable")
    # Do not renew here: future unsafe endpoints must validate CSRF first.
    return user
