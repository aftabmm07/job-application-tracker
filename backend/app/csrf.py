"""Session-bound HMAC tokens plus explicit origin/content-type request checks."""
from datetime import UTC, datetime
import hashlib
import hmac
import re
from urllib.parse import urlsplit

from fastapi import HTTPException
from starlette.requests import Request

from app.auth_config import AuthConfigurationError, AuthSettings, normalize_browser_origin
from app.models import AuthSession
from app.session_service import token_digest

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _reject(status_code=403, detail="Request security validation failed"):
    raise HTTPException(status_code=status_code, detail=detail, headers={"Cache-Control": "no-store"})


def create_csrf_token(raw_session_token: str, settings: AuthSettings) -> str:
    if token_digest(raw_session_token) is None:
        raise ValueError("A valid session token is required")
    return hmac.new(settings.csrf_hmac_secret,
                    b"csrf:v1:" + raw_session_token.encode("ascii"), hashlib.sha256).hexdigest()


def verify_csrf_token(raw_session_token: str | None, submitted_token: str | None, settings: AuthSettings) -> bool:
    if token_digest(raw_session_token) is None:
        return False
    if not isinstance(submitted_token, str) or not re.fullmatch(r"[0-9a-f]{64}", submitted_token):
        return False
    return hmac.compare_digest(create_csrf_token(raw_session_token, settings), submitted_token)


def require_trusted_origin(request: Request, settings: AuthSettings):
    origins = request.headers.getlist("origin")
    try:
        if origins:
            if len(origins) != 1:
                _reject()
            # A present but invalid Origin must NOT fall back to a valid Referer.
            source = normalize_browser_origin(origins[0], settings.environment == "production")
        else:
            referers = request.headers.getlist("referer")
            if len(referers) != 1:
                _reject()
            referer = referers[0]
            if any(ord(char) <= 32 or ord(char) == 127 for char in referer) or "\\" in referer:
                _reject()
            parsed = urlsplit(referer)
            if parsed.username is not None or parsed.password is not None or parsed.fragment:
                _reject()
            source = normalize_browser_origin(f"{parsed.scheme}://{parsed.netloc}", settings.environment == "production")
        if source not in settings.allowed_browser_origins:
            _reject()
    except (ValueError, AuthConfigurationError):
        _reject()


def require_json_content_type(request: Request):
    content_types = request.headers.getlist("content-type")
    if len(content_types) != 1:
        _reject(415, "Content-Type must be application/json")
    parts = [part.strip().lower() for part in content_types[0].split(";")]
    if parts[0] != "application/json" or any(part not in {"charset=utf-8", 'charset="utf-8"'} for part in parts[1:]):
        _reject(415, "Content-Type must be application/json")


def require_csrf(request: Request, session: AuthSession | None, settings: AuthSettings, *, now=None):
    """Supply a row from SessionService.lookup; renew only AFTER this succeeds."""
    if request.method in SAFE_METHODS:
        return
    require_trusted_origin(request, settings)
    require_json_content_type(request)
    raw_token = request.cookies.get(settings.session_cookie_name)
    digest = token_digest(raw_token)
    now = now if now is not None else datetime.now(UTC)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("CSRF clock must be timezone-aware")
    if (session is None or digest is None or not hmac.compare_digest(digest, session.token_hash)
            or not session.created_at <= session.last_activity_at <= now
            or now >= session.expires_at or now >= session.idle_expires_at):
        _reject()
    tokens = request.headers.getlist("x-csrf-token")
    if len(tokens) != 1 or not verify_csrf_token(raw_token, tokens[0], settings):
        _reject()
