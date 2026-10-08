from fastapi import FastAPI, HTTPException, Depends
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import engine, SessionLocal
from app.models import JobApplication
from app.schemas import (
    JobApplicationCreate,
    JobApplicationResponse,
    JobApplicationUpdate,
)

app = FastAPI(
    title="Job Application Tracker API",
    description="Backend API for managing job applications",
    version="0.1.0"
)


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
        with engine.connect() as connection:
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

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.post(
    "/applications",
    response_model=JobApplicationResponse,
    status_code=201,
)
def create_application(
    application: JobApplicationCreate,
    db: Session = Depends(get_db),
):
    new_application = JobApplication(**application.model_dump())

    try:
        db.add(new_application)
        db.commit()
        db.refresh(new_application)

    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(
            status_code=503,
            detail="Database operation failed",
        )

    return new_application


@app.get(
    "/applications",
    response_model=list[JobApplicationResponse],
)
def list_applications(
    db: Session = Depends(get_db),
):
    applications = (
        db.query(JobApplication)
        .order_by(JobApplication.id.desc())
        .all()
    )

    return applications


@app.get(
    "/applications/{application_id}",
    response_model=JobApplicationResponse,
)
def get_application(
    application_id: int,
    db: Session = Depends(get_db),
):
    application = db.get(JobApplication, application_id)

    if application is None:
        raise HTTPException(
            status_code=404,
            detail="Job application not found",
        )

    return application



@app.patch(
    "/applications/{application_id}",
    response_model=JobApplicationResponse,
)
def update_application(
    application_id: int,
    updates: JobApplicationUpdate,
    db: Session = Depends(get_db),
):
    application = db.get(JobApplication, application_id)

    if application is None:
        raise HTTPException(
            status_code=404,
            detail="Job application not found",
        )

    update_data = updates.model_dump(exclude_unset=True)

    try:
        for field, value in update_data.items():
            setattr(application, field, value)

        db.commit()
        db.refresh(application)

    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(
            status_code=503,
            detail="Database operation failed",
        )

    return application



@app.delete(
    "/applications/{application_id}",
    status_code=204,
)
def delete_application(
    application_id: int,
    db: Session = Depends(get_db),
):
    application = db.get(JobApplication, application_id)

    if application is None:
        raise HTTPException(
            status_code=404,
            detail="Job application not found",
        )

    try:
        db.delete(application)
        db.commit()

    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(
            status_code=503,
            detail="Database operation failed",
        )