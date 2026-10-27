from fastapi import FastAPI, HTTPException, Depends, Request
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.auth_config import AuthSettings, get_auth_settings
from app.email_delivery import build_email_sender
from app.auth_routes import router as auth_router
from app.dependencies import get_db
from app.session_http import no_store
from app.request_limits import RequestBodyLimitMiddleware
from app.database import get_database_engine
from app.models import JobApplication, User
from app.auth_dependencies import auth_error, get_auth_db, get_current_user, get_mutation_user
from app.session_service import SessionService, SessionStorageError, commit_session_changes
from app.schemas import (
    JobApplicationCreate,
    JobApplicationResponse,
    JobApplicationUpdate,
)

# Validate security configuration at startup, before accepting requests.
auth_settings = get_auth_settings()
email_sender = build_email_sender(auth_settings)

app = FastAPI(
    title="Job Application Tracker API",
    description="Backend API for managing job applications",
    version="0.1.0"
)
app.state.email_sender = email_sender

app.add_middleware(RequestBodyLimitMiddleware, max_bytes=auth_settings.json_max_body_bytes)


app.include_router(auth_router)


@app.middleware("http")
async def private_no_store(request, call_next):
    response = await call_next(request)
    if (request.url.path in {"/auth", "/applications"}
            or request.url.path.startswith(("/auth/", "/applications/"))):
        no_store(response)
    return response


@app.get("/")
def home():
    return {
        "message": "Welcome to Job Application Tracker API",
        "status": "running"
    }


@app.get("/health")
def health_check():
    return {
        "status": "healthy"
    }


@app.get("/health/db")
def database_health():
    try:
        with get_database_engine().connect() as connection:
            connection.execute(text("SELECT 1"))

        return {
            "status": "healthy",
            "database": "connected"
        }

    except SQLAlchemyError:
        raise HTTPException(
            status_code=503,
            detail="Database unavailable"
        )

def application_storage_error(db):
    try:
        db.rollback()
    except SQLAlchemyError:
        pass
    raise HTTPException(503, "Database operation failed", headers={"Cache-Control": "no-store"}) from None


def finish_application_request(db, request, settings):
    # Renewal and CRUD changes share one commit. A revoked/expired session cannot
    # leave a partially committed mutation behind. Absolute expiry never extends.
    raw = request.cookies.get(settings.session_cookie_name)
    if SessionService(db, settings).renew(raw) is None:
        db.rollback()
        auth_error(401, "Authentication required")
    commit_session_changes(db)


def missing_application():
    # Deliberately identical for absent, foreign-owned and NULL-owned records.
    raise HTTPException(404, "Job application not found")


@app.post("/applications", response_model=JobApplicationResponse, status_code=201)
def create_application(
    application: JobApplicationCreate, request: Request,
    user: User = Depends(get_mutation_user), db: Session = Depends(get_auth_db),
    settings: AuthSettings = Depends(get_auth_settings),
):
    try:
        record = JobApplication(**application.model_dump(), user_id=user.id)
        db.add(record)
        db.flush()
        # Snapshot the public schema before commit; no unscoped refresh/query.
        result = JobApplicationResponse.model_validate(record)
        finish_application_request(db, request, settings)
        return result
    except (SQLAlchemyError, SessionStorageError):
        application_storage_error(db)


@app.get("/applications", response_model=list[JobApplicationResponse])
def list_applications(
    request: Request, user: User = Depends(get_current_user),
    db: Session = Depends(get_auth_db), settings: AuthSettings = Depends(get_auth_settings),
):
    try:
        records = db.scalars(select(JobApplication).where(
            JobApplication.user_id == user.id,
        ).order_by(JobApplication.id.desc())).all()
        result = [JobApplicationResponse.model_validate(record) for record in records]
        finish_application_request(db, request, settings)
        return result
    except (SQLAlchemyError, SessionStorageError):
        application_storage_error(db)


@app.get("/applications/{application_id}", response_model=JobApplicationResponse)
def get_application(
    application_id: int, request: Request, user: User = Depends(get_current_user),
    db: Session = Depends(get_auth_db), settings: AuthSettings = Depends(get_auth_settings),
):
    try:
        record = db.scalar(select(JobApplication).where(
            JobApplication.id == application_id, JobApplication.user_id == user.id,
        ))
        if record is None:
            missing_application()
        result = JobApplicationResponse.model_validate(record)
        finish_application_request(db, request, settings)
        return result
    except (SQLAlchemyError, SessionStorageError):
        application_storage_error(db)


@app.patch("/applications/{application_id}", response_model=JobApplicationResponse)
def update_application(
    application_id: int, updates: JobApplicationUpdate, request: Request,
    user: User = Depends(get_mutation_user), db: Session = Depends(get_auth_db),
    settings: AuthSettings = Depends(get_auth_settings),
):
    try:
        ownership = (JobApplication.id == application_id, JobApplication.user_id == user.id)
        values = updates.model_dump(exclude_unset=True)
        if values:
            record = db.scalar(update(JobApplication).where(*ownership).values(**values)
                               .returning(JobApplication).execution_options(populate_existing=True))
        else:
            # Empty PATCH is a validated no-op; UPDATE SET with no fields is invalid SQL.
            record = db.scalar(select(JobApplication).where(*ownership))
        if record is None:
            missing_application()
        result = JobApplicationResponse.model_validate(record)
        finish_application_request(db, request, settings)
        return result
    except (SQLAlchemyError, SessionStorageError):
        application_storage_error(db)


@app.delete("/applications/{application_id}", status_code=204)
def delete_application(
    application_id: int, request: Request, user: User = Depends(get_mutation_user),
    db: Session = Depends(get_auth_db), settings: AuthSettings = Depends(get_auth_settings),
):
    try:
        deleted_id = db.scalar(delete(JobApplication).where(
            JobApplication.id == application_id, JobApplication.user_id == user.id,
        ).returning(JobApplication.id))
        if deleted_id is None:
            missing_application()
        finish_application_request(db, request, settings)
    except (SQLAlchemyError, SessionStorageError):
        application_storage_error(db)
