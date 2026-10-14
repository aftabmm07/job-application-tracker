"""Cookie/response helpers shared by authentication endpoints."""
from datetime import UTC, datetime

from starlette.responses import Response

from app.auth_config import AuthSettings
from app.session_service import IssuedSession


def no_store(response: Response):
    response.headers["Cache-Control"] = "no-store"


def set_session_cookie(response: Response, issued: IssuedSession, settings: AuthSettings, *, now=None):
    """Use only after commit_session_changes succeeds. Idle expiry is server-side."""
    now = now if now is not None else datetime.now(UTC)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Cookie clock must be timezone-aware")
    remaining = int((issued.expires_at - now).total_seconds())
    if remaining <= 0:
        raise ValueError("Cannot set an expired session cookie")
    response.set_cookie(
        key=settings.session_cookie_name, value=issued.raw_token,
        max_age=remaining, expires=issued.expires_at,
        path="/", secure=settings.session_cookie_secure,
        httponly=True, samesite="lax",  # No Domain: host-only, including __Host- cookies.
    )
    no_store(response)


def clear_session_cookie(response: Response, settings: AuthSettings):
    """Revoke and commit server-side first; deleting a cookie alone is not logout."""
    response.delete_cookie(
        key=settings.session_cookie_name, path="/", secure=settings.session_cookie_secure,
        httponly=True, samesite="lax",
    )
    no_store(response)
