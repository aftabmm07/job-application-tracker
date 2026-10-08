
# Development Setup Guide

## Prerequisites

- Git
- Visual Studio Code
- Docker Desktop with WSL 2 integration enabled
- Ubuntu WSL
- Python 3.14 with pip and venv support

## Clone the Repository

In your Ubuntu WSL terminal:

```bash
git clone https://github.com/aftabmm07/job-application-tracker.git
cd job-application-tracker
```

## PostgreSQL Setup

The application uses PostgreSQL 17 running in Docker.

### 1. Configure Environment Variables

Create a `.env` file in the project root:

```env
POSTGRES_PASSWORD=replace_with_your_local_password
```

Use your own password. Never commit the `.env` file.

### 2. Start PostgreSQL

From the project root:

```bash
docker compose up -d
```

### 3. Verify Database Status

```bash
docker compose ps
```

The `job-tracker-db` container should show a healthy status.

PostgreSQL is accessible locally at `127.0.0.1:5432`.

The Docker Compose configuration uses a named volume to persist database data.

## Backend Setup (Ubuntu WSL)

### 1. Navigate to the backend

```bash
cd backend
```

### 2. Create a Python Virtual Environment

For Linux-based development, create a virtual environment:

```bash
python3 -m venv ~/python-envs/job-tracker
```

Activate it:

```bash
source ~/python-envs/job-tracker/bin/activate
```

### 3. Install Dependencies

```bash
python -m pip install -r requirements.txt
```

Dependencies include FastAPI, Uvicorn, Pytest, HTTPX, SQLAlchemy, Psycopg, and python-dotenv.

### 4. Start the Backend Server

```bash
python -m uvicorn app.main:app --reload
```

The server runs at:

http://127.0.0.1:8000

## Available Endpoints

| Method | Endpoint | Description |
|---|---|---|
| GET | `/` | API welcome message |
| GET | `/health` | Basic API health check |
| GET | `/health/db` | PostgreSQL connectivity check |
| GET | `/docs` | Interactive API documentation |

## Database Connectivity

The backend uses SQLAlchemy with the Psycopg PostgreSQL driver.

Database connection configuration is defined in:

`backend/app/database.py`

The database connection uses:

- Host: `127.0.0.1`
- Port: `5432`
- Database: `jobtracker_db`
- Username: `jobtracker`
- Password: loaded from `POSTGRES_PASSWORD`

To verify connectivity, start the API and visit:

http://127.0.0.1:8000/health/db

Expected response:

```json
{
  "status": "healthy",
  "database": "connected"
}
```

If the database is unavailable, the endpoint returns HTTP 503.

## Running Backend Tests

The backend uses Pytest and FastAPI's TestClient.

From the `backend` directory, run:

```bash
python -m pytest -v
```

### Current Test Coverage

The four tests verify:

1. `GET /` returns a successful response.
2. `GET /health` returns a healthy status.
3. `GET /health/db` succeeds when the database connection is available.
4. `GET /health/db` returns HTTP 503 when the database connection fails.

The database health tests use mocking, so a running PostgreSQL instance is not required for these tests.

However, `POSTGRES_PASSWORD` must be configured when importing the application.

### CI Testing

GitHub Actions automatically runs the backend tests for pushes and pull requests targeting `main`.

The CI workflow supplies a dummy `POSTGRES_PASSWORD` environment variable for the mocked tests.

## Development Notes

- `.env` contains local credentials and must never be committed.
- Python virtual environments are excluded from Git.
- PostgreSQL data persists in a Docker named volume.
- `docker compose down` stops and removes the containers without deleting the named database volume.
- `GET /health` checks the API; `GET /health/db` additionally checks database connectivity.
