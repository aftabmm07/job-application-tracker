"""Authentication endpoints sharing the same sessions used by protected CRUD."""
import secrets

from fastapi import APIRouter, Depends, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

from app.auth_config import AuthSettings, get_auth_settings
from app.auth_dependencies import auth_error, get_auth_db, get_current_user, get_mutation_user
from app.auth_limits import AuthRateLimiter, get_auth_limiter
from app.auth_schemas import (
    Credentials, CsrfResponse, EmailVerificationRequest, EmailVerificationResponse,
    IdentityResponse, ProfileResponse, ProfileUpdate, RegistrationResponse,
    ResendVerificationRequest, ResendVerificationResponse,
)
from app.csrf import create_csrf_token, require_csrf, require_json_content_type, require_trusted_origin
from app.email_verification import (
    VerificationStorageError, activate_and_commit_verified_user, add_registration_verification_token,
    resend_verification_for_user,
)
from app.development_verification_preview import development_previews
from app.email_delivery import EmailSender, get_email_sender
from app.models import User
from app.passwords import PasswordWorkBusy, hash_password, verify_and_upgrade_password
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
            except PasswordWorkBusy:
                response = JSONResponse(
                    {"detail": "Authentication service busy. Please try again later."},
                    status_code=503, headers={"Retry-After": "1"},
                )
            except (SessionStorageError, VerificationStorageError, SQLAlchemyError):
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
             limiter: AuthRateLimiter = Depends(get_auth_limiter),
             sender: EmailSender | None = Depends(get_email_sender)):
    unsafe_session(request, db, settings)
    limiter.check([("register-ip", request_ip(request), 10, 900)])
    # Never create an unusable production account or use the debugger preview
    # as a production delivery fallback.
    if settings.environment == "production" and sender is None:
        auth_error(503, "Authentication service unavailable")
    if sender is not None and settings.verification_frontend_origin is None:
        auth_error(503, "Authentication service unavailable")
    # Hash on both new and duplicate paths; never disclose an existing account.
    encoded = hash_password(credentials.password.get_secret_value())
    if db.scalar(select(User.id).where(User.email == credentials.email)) is None:
        try:
            user = User(email=credentials.email, password_hash=encoded)
            db.add(user)
            # Flush obtains the ID and checks uniqueness without committing.
            # Both inserts roll back if token creation or the final commit fails.
            db.flush()
            issued = add_registration_verification_token(db, user.id, settings=settings)
            # Check token constraints before calling an external side effect.
            db.flush()
            if sender is not None:
                try:
                    sender.send_verification(user.email, issued.link, issued.expires_at)
                except Exception:
                    # Delivery failed before commit. Preserve the generic response
                    # for both unknown and existing email addresses.
                    db.rollback()
                    return RegistrationResponse()
            db.commit()
        except IntegrityError:
            db.rollback()
            # Another request may have inserted the same normalized email.
            if db.scalar(select(User.id).where(User.email == credentials.email)) is None:
                auth_error(503, "Authentication service unavailable")
        else:
            if issued.link is not None and settings.dev_email_verification_preview:
                try:
                    development_previews.publish(issued, settings)
                except Exception:
                    # Best-effort local tooling must not change the generic
                    # registration response after a successful commit. No logs.
                    pass
    return RegistrationResponse()


@router.post("/resend-verification", response_model=ResendVerificationResponse, status_code=202)
def resend_verification(payload: ResendVerificationRequest, request: Request,
                        db: Session = Depends(get_auth_db), settings: AuthSettings = Depends(get_auth_settings),
                        limiter: AuthRateLimiter = Depends(get_auth_limiter),
                        sender: EmailSender | None = Depends(get_email_sender)):
    unsafe_session(request, db, settings)
    reply = ResendVerificationResponse()
    try:
        # Separate checks count every allowed attempt against the IP even when
        # that email has exhausted its own limit. Both precede account lookup.
        limiter.check([("resend-ip", request_ip(request), 20, 900)])
        limiter.check([("resend-email", payload.email, 3, 3600)])
    except HTTPException as error:
        if error.status_code != 429:
            raise
        # Do not forward Retry-After or distinguish rate-limited addresses.
        return reply
    if sender is None or settings.verification_frontend_origin is None:
        # Never issue undeliverable tokens or use the private preview as fallback.
        return reply
    user_id = db.scalar(select(User.id).where(User.email == payload.email))
    if user_id is not None:
        # The service rechecks eligibility and cooldown under the user lock,
        # handles delivery/commit, and returns no account-specific HTTP data.
        resend_verification_for_user(db=db, user_id=user_id, sender=sender, settings=settings)
    return reply


@router.post("/verify-email", response_model=EmailVerificationResponse)
def verify_email(payload: EmailVerificationRequest, request: Request,
                 db: Session = Depends(get_auth_db), settings: AuthSettings = Depends(get_auth_settings),
                 limiter: AuthRateLimiter = Depends(get_auth_limiter)):
    unsafe_session(request, db, settings)
    limiter.check([("verify-email-ip", request_ip(request), 20, 900)])
    # No query parameters are supported. Clients must never put bearer tokens
    # in URLs: incoming URLs may already have been logged by a proxy/server.
    if request.query_params:
        auth_error(400, "Invalid email verification request")
    if not activate_and_commit_verified_user(db=db, raw_token=payload.token.get_secret_value()):
        auth_error(400, "Invalid or unavailable verification token")
    # Activation commits before returning; no session/cookie is created or rotated.
    return EmailVerificationResponse()


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


@router.get("/profile", response_model=ProfileResponse)
def get_profile(request: Request, user: User = Depends(get_current_user),
                db: Session = Depends(get_auth_db),
                settings: AuthSettings = Depends(get_auth_settings)):
    result = ProfileResponse.model_validate(user)
    if SessionService(db, settings).renew(request.cookies.get(settings.session_cookie_name)) is None:
        auth_error(401, "Authentication required")
    commit_session_changes(db)
    return result


@router.patch("/profile", response_model=ProfileResponse)
def update_profile(updates: ProfileUpdate, request: Request,
                   user: User = Depends(get_mutation_user),
                   db: Session = Depends(get_auth_db),
                   settings: AuthSettings = Depends(get_auth_settings)):
    values = updates.model_dump(exclude_unset=True)
    if values:
        profile = db.scalar(
            update(User)
            .where(User.id == user.id, User.is_active.is_(True), User.email_verified_at.is_not(None))
            .values(**values)
            .returning(User)
            .execution_options(populate_existing=True)
        )
    else:
        profile = user
    if profile is None:
        auth_error(401, "Authentication required")
    result = ProfileResponse.model_validate(profile)
    if SessionService(db, settings).renew(request.cookies.get(settings.session_cookie_name)) is None:
        db.rollback()
        auth_error(401, "Authentication required")
    commit_session_changes(db)
    return result


@router.post("/logout", status_code=204)
def logout(request: Request, db: Session = Depends(get_auth_db),
           settings: AuthSettings = Depends(get_auth_settings)):
    service, record, raw = unsafe_session(request, db, settings)
    if not service.revoke(raw):
        auth_error(403, "Request security validation failed")
    commit_session_changes(db)
    response = Response(status_code=204)
    clear_session_cookie(response, settings)
    return response
