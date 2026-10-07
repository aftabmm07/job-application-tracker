
# Development Setup Guide

## Prerequisites

- Git
- Python 3.14.8 (current development environment)
- Visual Studio Code

## Clone the Repository

```powershell
git clone https://github.com/aftabmm07/job-application-tracker.git
cd job-application-tracker
```

## Backend Setup

### 1. Navigate to the backend

```powershell
cd backend
```

### 2. Create a virtual environment

```powershell
python -m venv .venv
```

### 3. Install dependencies

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### 4. Start the development server

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

## Available Endpoints

| Method | Endpoint | Description |
|---|---|---|
| GET | `/` | API welcome message |
| GET | `/health` | Basic API health check |
| GET | `/docs` | Interactive API documentation |

## Local URLs

- API: http://127.0.0.1:8000
- Health: http://127.0.0.1:8000/health
- Swagger UI: http://127.0.0.1:8000/docs

## Notes

- The virtual environment is excluded from Git.
- Dependencies are listed in `backend/requirements.txt`.
- Use `python -m pip` rather than running `pip.exe` directly if Windows Application Control blocks it.
- The current health endpoint only checks that the API responds; it does not check database connectivity.


## Running Backend Tests

The backend uses Pytest for automated testing.

### Prerequisites

Install the backend dependencies inside the Python virtual environment:

```powershell
cd backend
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### Run All Tests

From the `backend` directory, execute:

```powershell
.\.venv\Scripts\python.exe -m pytest -v
```

### Current Test Coverage

The initial test suite verifies:

- `GET /` returns HTTP 200 and the expected welcome response.
- `GET /health` returns HTTP 200 and a healthy status.

### Expected Result

Both tests should pass:

- `test_home` — PASSED
- `test_health` — PASSED

### Notes

- Tests use FastAPI's `TestClient`.
- HTTPX provides HTTP client functionality for testing.
- Pytest cache files are excluded from Git.
- The API server does not need to be running separately during these tests.
