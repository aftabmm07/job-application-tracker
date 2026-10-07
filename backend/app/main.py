
from fastapi import FastAPI

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
