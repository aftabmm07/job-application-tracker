
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

### 4. Initialize the Database Tables

After installing the backend dependencies and starting PostgreSQL, initialize the application tables.

From the `backend` directory, run:

```bash
python -c "from app.database import engine; from app.models import Base; Base.metadata.create_all(bind=engine)"
```

This creates the `job_applications` table if it does not already exist.

The table stores:

- Company name
- Job title
- Location
- Application status
- Application date
- Job description
- Record creation timestamp

To inspect the table, temporarily return to the project root:

```bash
cd ..
docker compose exec db psql -U jobtracker -d jobtracker_db -c '\d job_applications'
cd backend
```

**Note:** `create_all()` is used for initial development setup. Database schema migrations will be introduced with Alembic in a future phase.

### 5. Start the Backend Server

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
| POST | `/applications` | Create a job application |
| GET | `/applications` | List all job applications |
| GET | `/applications/{application_id}` | Retrieve an application by ID |
| PATCH | `/applications/{application_id}` | Update an existing application |
| DELETE | `/applications/{application_id}` | Delete an application |
| GET | `/docs` | Interactive Swagger API documentation |

### Application Status Values

Supported statuses are:

- `saved`
- `applied`
- `interview`
- `offer`
- `rejected`

### Example: Create a Job Application

Send a `POST` request to `/applications` with:

```json
{
  "company_name": "Example Tech GmbH",
  "job_title": "Working Student DevOps",
  "location": "Dortmund",
  "status": "applied",
  "application_date": "2026-10-08",
  "job_description": "Support CI/CD pipelines and cloud infrastructure."
}
```

A successful request returns HTTP `201 Created` with the stored application, including its generated ID and creation timestamp.

Use http://127.0.0.1:8000/docs to test the endpoints interactively.

### API Validation and Errors

- `422` — Invalid request data
- `404` — Requested application does not exist
- `503` — Database operation fails or database health check fails

Company names and job titles cannot be empty or contain only whitespace.


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

The original backend coverage includes **12 API tests**, supplemented by
configuration and password-security tests in Phase 14.

Four tests cover API and database health:

1. API welcome endpoint
2. API health endpoint
3. Successful database connectivity check
4. Database connectivity failure handling

Eight tests cover job application management:

1. Create an application
2. List applications
3. Retrieve an application by ID
4. Update an application
5. Delete an application
6. Reject invalid application creation data
7. Reject null values for required fields during updates
8. Reject whitespace-only company names and job titles

Application CRUD tests use an isolated in-memory SQLite database with FastAPI dependency overrides.

This prevents test records from affecting the development PostgreSQL database.

The database health tests use mocking.

A running PostgreSQL container is not required for these automated tests, but `POSTGRES_PASSWORD` must be configured when importing the application.


### CI Testing

GitHub Actions automatically runs the backend tests for pushes and pull requests targeting `main`.

The CI workflow supplies a dummy `POSTGRES_PASSWORD` environment variable for the mocked tests.

## Development Notes

- `.env` contains local credentials and must never be committed.
- Python virtual environments are excluded from Git.
- PostgreSQL data persists in a Docker named volume.
- `docker compose down` stops and removes the containers without deleting the named database volume.
- `GET /health` checks the API; `GET /health/db` additionally checks database connectivity.

## Authentication foundations (Phase 14)

This checkpoint adds validated configuration and password hashing only. It does
not enable authentication or create users, sessions, endpoints, or database tables.
Application startup calls `get_auth_settings()` so invalid production settings
stop startup. Existing health endpoints and CRUD behavior remain unchanged.

Settings are read from the project-root `.env` and exported environment variables;
exported variables take precedence. Settings are cached per process, so restart
Uvicorn after changing them. Never commit `.env` or log configuration dictionaries.

| Variable | Development default | Production requirement / validation |
|---|---|---|
| `APP_ENV` | `development` | Set explicitly to `production`; only these two values are accepted. Deployment must set it correctly. |
| `SESSION_COOKIE_NAME` | `session` | Defaults to `__Host-session`; custom production names must use `__Host-`. Maximum 128 valid cookie-name characters. |
| `SESSION_COOKIE_SECURE` | `false` | Defaults to `true` in production; explicit `false` is rejected. Only `true` / `false` accepted. |
| `SESSION_ABSOLUTE_TIMEOUT_SECONDS` | `28800` (8 hours) | Optional; integer 1–604800 (7 days). |
| `SESSION_IDLE_TIMEOUT_SECONDS` | `1800` (30 minutes) | Optional; integer 1–86400 (24 hours), never greater than the absolute timeout. |
| `CSRF_HMAC_SECRET` | Fresh random 32-byte process-local key when omitted | Required; 32–1024 UTF-8 bytes, not whitespace-only. Supply a cryptographically random secret from your secret manager. |
| `AUTH_ALLOWED_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | Required comma-separated exact HTTPS origins. No wildcard, credentials, path, trailing slash, query, fragment, or empty entries. |

`POSTGRES_PASSWORD` remains required by the existing database configuration.
`python-dotenv==1.2.4` is now explicitly declared, alongside `pwdlib[argon2]==0.3.1`.
Origin schemes/hostnames are normalized and default ports removed; duplicates are
removed. Origins refer to the browser-facing frontend (including its Vite port),
not just the proxy target `127.0.0.1:8000`. Use localhost or 127.0.0.1 consistently
in the browser because cookies are separate for these hosts.

The development fallback CSRF key changes on process restart and is unsuitable
for multiple workers. Production must use the same securely generated key across
workers; no fallback is allowed. Length checks cannot prove entropy: do not use a
memorable phrase or repeated characters. Generate at least 32 random bytes with a
trusted secret manager or Python's `secrets.token_urlsafe(32)`, and transfer the
result directly into private deployment configuration. Never put it in docs,
command-line arguments, logs, Git, or chat. No production secret value is supplied
here. Secret values are excluded from the settings object's repr, and validation
errors identify variables without echoing their supplied values.

These are configuration foundations, not cookie/CSRF implementation. When cookies
are introduced, production must use HTTPS, HttpOnly, SameSite=Lax, Path=/ and no
Domain attribute (required for the __Host- prefix). Local HTTP development uses
Secure=false; do not rely on browser-specific localhost exceptions in production.

### Password helpers

`app/passwords.py` uses pwdlib with explicit Argon2id, 65536 KiB (64 MiB) memory,
three iterations and parallelism four. Each hash gets a fresh random salt. Benchmark
these settings on deployment hardware before enabling public authentication.

Passwords must be strings of **15–128 Unicode code points** and valid UTF-8.
Spaces, including leading/trailing spaces, are preserved. No trimming, truncation,
Unicode normalization, or composition rules are applied. The 128-character maximum
bounds input size while allowing passphrases; UTF-8 encoding may use multiple bytes
per character. Password validation raises fixed messages without including input.

- `hash_password(password)` validates then hashes.
- `verify_password(password, stored_hash)` returns a boolean; invalid input or
  malformed hashes fail safely.
- `password_hash_needs_upgrade(stored_hash)` checks current algorithm/parameters;
  malformed hashes are flagged for replacement but never prove authentication.
- `verify_and_upgrade_password(password, stored_hash)` returns `(valid, replacement)`;
  replacement is only generated after successful verification. Future login code
  must persist it transactionally. No database writes happen in these helpers.

No helper logs passwords or hashes. Future request schemas/handlers must also
remove password input from HTTP validation errors and logs; do not serialize
Pydantic error dictionaries containing raw password input. Password input must
never be included in public responses or access-log metadata.

### Security tests

Run all tests from `backend/` with `python -m pytest -v`. The new test modules cover
Argon2id format and salts, verification, Unicode/length boundaries, exact password
preservation, malformed hashes, successful upgrades, development defaults,
production requirements, insecure configuration, and secret-safe errors/repr.
Existing CRUD tests still use in-memory SQLite; health checks still mock PostgreSQL.
CI needs no new secrets under the default development environment. Production
configuration tests use isolated mappings and synthetic test-only values.
