"""Authentication endpoints sharing the same sessions used by protected CRUD."""
import secrets

from fastapi import APIRouter, Depends, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

from app.auth_config import AuthSettings, get_auth_settings
from app.auth_dependencies import auth_error, get_auth_db, get_current_user
from app.auth_limits import AuthRateLimiter, get_auth_limiter
from app.auth_schemas import Credentials, CsrfResponse, IdentityResponse, RegistrationResponse
from app.csrf import create_csrf_token, require_csrf, require_json_content_type, require_trusted_origin
from app.models import User
from app.passwords import hash_password, verify_and_upgrade_password
from app.session_http import clear_session_cookie, no_store, set_session_cookie
from app.session_service import InvalidSessionError, SessionService, SessionStorageError, commit_session_changes

# Precompute once, not on each unknown-email request. No usable account or credential.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(32))


class AuthRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request):
            try:
                if request.method not in {"GET", "HEAD", "OPTIONS"}:
                    # Reject unsupported media types before parsing credentials.
                    require_json_content_type(request)
                response = await original(request)
            except RequestValidationError:
                # Pydantic error input/ctx can contain plaintext credentials.
                response = JSONResponse({"detail": "Invalid authentication request"}, status_code=422)
            except HTTPException as error:
                response = JSONResponse({"detail": error.detail}, status_code=error.status_code, headers=error.headers)
            except (SessionStorageError, SQLAlchemyError):
                response = JSONResponse({"detail": "Authentication service unavailable"}, status_code=503)
            except Exception:
                # No exception repr/traceback: unexpected auth failures may contain inputs.
                response = JSONResponse({"detail": "Authentication service unavailable"}, status_code=500)
            if response.status_code >= 400:
                # Errors are converted here before yield dependencies unwind, so
                # explicitly roll back any pending auth transaction as well.
                db = getattr(request.state, "auth_db", None)
                if db is not None:
                    try:
                        db.rollback()
                    except SQLAlchemyError:
                        pass
            no_store(response)
            return response
        return handler


router = APIRouter(prefix="/auth", tags=["authentication"], route_class=AuthRoute)


def request_ip(request):
    # Do not trust client-controlled X-Forwarded-For. Configure a trusted proxy separately.
    return request.client.host if request.client else "unknown-client"


def unsafe_session(request, db, settings):
    raw = request.cookies.get(settings.session_cookie_name)
    service = SessionService(db, settings)
    record = service.lookup(raw)
    require_csrf(request, record, settings)
    return service, record, raw


@router.get("/csrf", response_model=CsrfResponse)
def csrf_bootstrap(request: Request, response: Response,
                   db: Session = Depends(get_auth_db), settings: AuthSettings = Depends(get_auth_settings),
                   limiter: AuthRateLimiter = Depends(get_auth_limiter)):
    require_trusted_origin(request, settings)
    limiter.check([("bootstrap-ip", request_ip(request), 60, 60)])
    service = SessionService(db, settings)
    raw = request.cookies.get(settings.session_cookie_name)
    if service.lookup(raw) is None:
        issued = service.create()
        commit_session_changes(db)
        set_session_cookie(response, issued, settings)
        raw = issued.raw_token
    no_store(response)
    return CsrfResponse(csrf_token=create_csrf_token(raw, settings))


@router.post("/register", response_model=RegistrationResponse, status_code=202)
def register(credentials: Credentials, request: Request,
             db: Session = Depends(get_auth_db), settings: AuthSettings = Depends(get_auth_settings),
             limiter: AuthRateLimiter = Depends(get_auth_limiter)):
    unsafe_session(request, db, settings)
    limiter.check([("register-ip", request_ip(request), 10, 900)])
    # Hash on both new and duplicate paths; never disclose an existing account.
    encoded = hash_password(credentials.password.get_secret_value())
    if db.scalar(select(User.id).where(User.email == credentials.email)) is None:
        db.add(User(email=credentials.email, password_hash=encoded))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            # Another request may have inserted the same normalized email.
            if db.scalar(select(User.id).where(User.email == credentials.email)) is None:
                auth_error(503, "Authentication service unavailable")
    return RegistrationResponse()


@router.post("/login", response_model=IdentityResponse)
def login(credentials: Credentials, request: Request, response: Response,
          db: Session = Depends(get_auth_db), settings: AuthSettings = Depends(get_auth_settings),
          limiter: AuthRateLimiter = Depends(get_auth_limiter)):
    service, record, raw = unsafe_session(request, db, settings)
    if record.user_id is not None:
        auth_error(403, "Request security validation failed")
    limiter.check([("login-ip", request_ip(request), 30, 300),
                   ("login-email", credentials.email, 10, 300)])
    user = db.scalar(select(User).where(User.email == credentials.email))
    valid, replacement = verify_and_upgrade_password(
        credentials.password.get_secret_value(), user.password_hash if user is not None else _DUMMY_HASH,
    )
    if not valid or user is None or not user.is_active or user.email_verified_at is None:
        auth_error(401, "Invalid credentials")
    identity = IdentityResponse.model_validate(user)
    if replacement is not None:
        user.password_hash = replacement
    try:
        issued = service.rotate_for_login(raw, user.id)
    except InvalidSessionError:
        auth_error(401, "Invalid credentials")
    commit_session_changes(db)
    set_session_cookie(response, issued, settings)
    return identity


@router.get("/me", response_model=IdentityResponse)
def me(request: Request, user: User = Depends(get_current_user),
       db: Session = Depends(get_auth_db), settings: AuthSettings = Depends(get_auth_settings)):
    identity = IdentityResponse.model_validate(user)
    service = SessionService(db, settings)
    if service.renew(request.cookies.get(settings.session_cookie_name)) is None:
        auth_error(401, "Authentication required")
    commit_session_changes(db)
    return identity


@router.post("/logout", status_code=204)
def logout(request: Request, db: Session = Depends(get_auth_db),
           settings: AuthSettings = Depends(get_auth_settings)):
    service, record, raw = unsafe_session(request, db, settings)
    service.revoke(raw)
    commit_session_changes(db)
    response = Response(status_code=204)
    clear_session_cookie(response, settings)
    return response
