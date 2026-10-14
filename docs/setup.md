
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

### 4. Versioned database setup

Use Alembic for database setup and upgrades; `Base.metadata.create_all()` is only
used by isolated model/API unit tests. See the migration procedure below before
running any database commands. This phase deliberately blocks `jobtracker_db`
from migration tooling. Never apply this branch to the original database without
a separately approved deployment/migration plan.

The new models require revision `0002_auth_models`. Existing PostgreSQL instances
still on the old schema cannot serve ORM application queries from this branch
until a separately approved migration is completed. Unit tests use isolated SQLite.

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

GitHub Actions runs the backend tests and a fresh PostgreSQL 17 migration/metadata
check for pushes and pull requests targeting `main`. The CI database is explicitly
named `jobtracker_auth_ci`; SQLite migration tests also cover populated legacy data.

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

## Versioned schema and authentication models (Phase 14)

`alembic==1.20.0` introduces two reviewed revisions:

- `0001_legacy` exactly reproduces the original `job_applications` table. It does
  not add server defaults that were previously only Python/ORM defaults.
- `0002_auth_models` adds `users`, `sessions`, and nullable/indexed application
  `user_id` referencing users with ON DELETE RESTRICT. It never assigns owners,
  changes application values, or creates accounts/sessions. PostgreSQL uses
  additive ALTER TABLE. SQLite uses Alembic batch copy/recreation to add the FK;
  tests compare every original value and verify all owners remain NULL.

Users have normalized unique email, an Argon2id-format password hash, UTC creation
and optional email verification timestamps, and `is_active=false` by default.
A database check prevents activation before email verification. The hash-prefix
check is a storage guard, not password verification; use the password helpers.
Email normalization is trim/lowercase; syntax validation belongs to future request
schemas. Database uniqueness and normalization checks also protect direct inserts.

Sessions store **32-byte binary SHA-256 digests**, never raw bearer tokens, with a
unique index. Nullable user_id supports anonymous pre-login sessions. User deletion
cascades to sessions; owned applications prevent user deletion instead of being
silently removed or orphaned. Absolute and idle expiration indexes support cleanup.
Checks enforce creation <= last activity < idle expiry <= absolute expiry.
UTCDateTime rejects naive writes, normalizes aware values to UTC, and restores UTC
awareness when SQLite returns naive UTC. Existing application creation timestamp
SQL type is unchanged. SQLite foreign keys are enabled for every test connection.

**Security boundary:** CRUD authorization is not implemented in this checkpoint.
Existing endpoints still expose all rows on their configured development database;
new nullable ownership alone does not isolate legacy records. Keep this intermediate
branch private/local. Do not deploy publicly until authentication and owner-scoped
queries are implemented. Verified ownership backfill is a later, explicit operation.

### Migration target safety

Migration commands never import `app.database` or fall back to its original target.
They require `MIGRATION_DATABASE_NAME`, reject `jobtracker_db` and system database
names before connecting, and verify PostgreSQL's actual current_database and public
schema. No command silently stamps a database. Offline mode and downgrades are
intentionally disabled: adoption requires live schema verification, and reversing
auth tables/ownership needs a separate data-preservation plan.

Optional connection overrides are `MIGRATION_DATABASE_HOST` (127.0.0.1),
`MIGRATION_DATABASE_PORT` (5432), and `MIGRATION_DATABASE_USER` (jobtracker).
`POSTGRES_PASSWORD` is loaded from the existing private .env/exported environment.
Never put passwords in CLI arguments or print database URLs.

### Fresh temporary database

Obtain approval for the exact database and command first. Have the operator create
an empty, explicitly named temporary database, then from `backend/` run:

```bash
MIGRATION_DATABASE_NAME=jobtracker_auth_fresh_9b2 python -m alembic upgrade head
MIGRATION_DATABASE_NAME=jobtracker_auth_fresh_9b2 python -m alembic current
MIGRATION_DATABASE_NAME=jobtracker_auth_fresh_9b2 python -m alembic check
```

Fresh databases run both revisions, without stamping. Never stamp head to skip
schema creation.

### Existing legacy database: verify, explicitly stamp, then upgrade

Only use an approved temporary clone of a verified restored backup for rehearsal.
Never run these commands against the original `jobtracker_db`. Before executing,
record the target name, confirm backup/restore validation, and approve the commands:

```bash
MIGRATION_DATABASE_NAME=jobtracker_auth_legacy_9b2 python -m scripts.verify_legacy_schema
MIGRATION_DATABASE_NAME=jobtracker_auth_legacy_9b2 python -m alembic stamp 0001_legacy
MIGRATION_DATABASE_NAME=jobtracker_auth_legacy_9b2 python -m alembic upgrade head
MIGRATION_DATABASE_NAME=jobtracker_auth_legacy_9b2 python -m alembic check
```

The verifier is read-only. It checks exact table/column types, lengths, nullability,
primary key, server defaults/serial sequence, constraints/indexes and unexpected
views/triggers. Drift must be investigated, never bypassed by stamping. The Alembic
environment repeats verification for unversioned existing tables and refuses to
skip directly to head. Stamping only records adoption; the additive revision adds
the new schema. Compare counts AND every legacy field, not counts alone. Confirm
all legacy user_id values remain NULL and users/sessions are empty.

For this phase, original-database adoption is deliberately blocked, not authorized.
When ownership enforcement exists, an independently reviewed migration/deployment
plan must precede any original-database upgrade. Never automatically assign old
records. Preserve unverified records and require an audited, verified owner mapping.

### Reproducible PostgreSQL rehearsal

The following script requires explicit approval before execution:

```bash
MIGRATION_DATABASE_NAME=jobtracker_auth_fresh_9b2 python -m scripts.rehearse_postgres
```

It creates NEW databases `jobtracker_auth_fresh_9b2` and
`jobtracker_auth_legacy_9b2`, aborting if either already exists. It reads only from
`jobtracker_restore_test`, streams pg_dump into pg_restore on the new legacy clone,
and does not access or alter the existing backup artifact. It verifies/stamps only
the clone, upgrades both targets, compares legacy record fingerprints/counts and
NULL ownership, checks metadata consistency, and confirms the source is unchanged.
It assumes the existing `job-tracker-db` Docker container and local jobtracker role.
No databases are dropped or cleaned up. Inspect any failed partial rehearsal;
do not overwrite or reuse it automatically. The manual commands above and this
script are alternative procedures, not steps to run consecutively on the same DB.

### Migration and model tests

From `backend/`, run `python -m pytest -v`. New test modules `test_models.py` and
`test_migrations.py` use only temporary SQLite databases and enforce foreign keys.
They cover fresh upgrades, populated unversioned baseline adoption, exact record
preservation, drift rejection, metadata alignment, uniqueness, ownership/session
FKs, activation checks, expiration checks, UTC handling, and disabled downgrades.
CI additionally runs `alembic upgrade head` / `alembic check` against its disposable
PostgreSQL service. Rehearsal databases are separate from all pytest databases.

## Session and CSRF foundations (Phase 14, checkpoint 9B.3)

These helpers are not attached to endpoints yet. Public health routes and existing
application CRUD retain their current behavior. No registration, login, me, logout,
frontend integration, PostgreSQL writes, or migrations are introduced here.

### Session service and transaction boundary

`app/session_service.py` operates on the existing AuthSession model. Construct
`SessionService(db, auth_settings)` with a request-scoped SQLAlchemy Session.
Tests inject a UTC clock; application code uses the UTC system clock.

- `create()` issues an anonymous pre-login session; `create(user_id)` issues an
  authenticated session only for an active, email-verified user. Password checking
  and account authorization remain the future endpoint's responsibility.
- Tokens use `secrets.token_urlsafe(32)` (256 random bits). Only the binary SHA-256
  digest is attached to ORM records. Raw tokens exist transiently in an IssuedSession
  result with repr disabled for its token; never serialize or log that object.
- Anonymous absolute lifetime is capped at 15 minutes and the configured absolute
  timeout, whichever is shorter. Its idle expiry is capped by that deadline.
  Authenticated sessions use the configured absolute and idle timeouts.
- `lookup(raw_token)` checks the database, active/verified user eligibility, absolute
  and idle expiry, and timestamps. The exact expiry instant is invalid. Lookup does
  not update activity or delete expired records. Invalid/missing token syntax fails
  closed without querying. Disabling an account invalidates its sessions immediately.
- `renew(raw_token)` conditionally updates last activity and idle expiry only while
  the stored row remains valid. Idle expiry is capped by the original absolute
  deadline. It cannot recreate a revoked row, revive an expired session, or move
  activity backwards when an older request arrives late.
- `revoke(raw_token)` deletes that session only; repeated revocation is harmless.
- `rotate_for_login(anonymous_token, user_id)` requires a live anonymous session,
  locks it on PostgreSQL, issues a fresh authenticated token, and removes the old
  row. Both changes belong to one transaction. Existing authenticated sessions are
  not accepted as anonymous login sessions. Repeated consumption is rejected.

The service flushes changes but **never commits automatically**. Call
`commit_session_changes(db)` only when the entire endpoint operation is ready.
Set cookies only after that call succeeds. On any service database failure, the
transaction rolls back and a generic SessionStorageError is raised without driver
messages or token values. Commit failures also roll back. Future endpoints must
translate this error to a generic 503 with Cache-Control: no-store, and must not
continue using a provisional token. A caller rollback restores the old anonymous
session and removes the uncommitted replacement during failed rotation. Rollbacks
apply to the request's whole unit of work; do not share its Session across requests.

### Cookie and response helpers

`app/session_http.py` exposes `set_session_cookie`, `clear_session_cookie`, and
`no_store`. Cookies are HttpOnly, SameSite=Lax, Path=/, with no Domain attribute.
Secure follows validated environment configuration; production uses __Host-session.
Cookie Max-Age reflects remaining absolute lifetime, not a fresh full timeout.
Server-side expiry is always authoritative, even if the browser still sends a
cookie. Revocation must commit before clearing a cookie. Cookie helpers mark the
response Cache-Control: no-store. Future auth endpoints must also apply no_store
on other successes and failures. Never place raw session tokens in JSON, URLs,
localStorage, logs, or diagnostics; only the HttpOnly cookie carries them to clients.

### CSRF and request validation

`app/csrf.py` derives a session-bound CSRF token as the hexadecimal HMAC-SHA-256 of
`b"csrf:v1:" + raw_session_token` under CSRF_HMAC_SECRET. The token is reproducible
for the current session, independently authenticated, and stored nowhere in the
session table. `verify_csrf_token` compares well-formed tokens with compare_digest.
Rotation changes the CSRF token; a token from another session or secret is invalid.
Only the CSRF token, never the HMAC secret or raw bearer token, may be returned by
the future bootstrap endpoint. CSRF token responses must not be cached or logged.

For unsafe requests, `require_csrf(request, session, settings)` requires:

1. One exact Origin matching configured browser origins, after canonical host and
   default-port normalization. Do not infer trust from Host or forwarded headers.
2. Only when Origin is entirely absent, one absolute HTTP(S) Referer whose origin
   exactly matches. Referer paths/queries are allowed, but credentials, fragments,
   controls, whitespace and backslashes are rejected. Missing both headers fails
   closed. A present empty, null, malformed or untrusted Origin never falls back
   to Referer. Duplicate security headers are rejected.
3. Content-Type application/json, optionally with UTF-8 charset. Other media types,
   encodings and parameters return 415. This requirement includes future bodyless
   DELETE/logout requests: their clients must still explicitly set application/json.
4. A live session row obtained from SessionService.lookup, matching the cookie's
   token hash, and one valid X-CSRF-Token header. Anonymous pre-login sessions are
   supported. Missing/invalid/cross-session tokens or expired sessions return 403.

Security exceptions carry generic messages and Cache-Control: no-store. GET, HEAD
and OPTIONS are not CSRF-gated by require_csrf; they must never perform application
mutations. The future bootstrap endpoint should call require_trusted_origin
explicitly before creating an anonymous session. SameSite is defense in depth,
not a replacement for token/origin validation. XSS remains outside CSRF protection.

### Future endpoint integration order

For an unsafe application request: lookup without renewal, validate CSRF/origin/
content type, require an authenticated eligible user and enforce query ownership,
perform the operation, renew activity, then commit the unit of work. Invalid CSRF
requests must not prolong sessions. Roll back if any check or operation fails.

For future login: lookup the anonymous session, validate CSRF and input, verify
credentials, rotate_for_login, commit, then set the new cookie and return only public
user data. Obtain a fresh CSRF token for the rotated session. For logout: validate
CSRF, revoke, commit, then clear the cookie. Auth response/error handling must always
apply no-store. None of these endpoints exist in this checkpoint.

In Phase 15, React will fetch a CSRF bootstrap response, keep the token in memory,
and send X-CSRF-Token with application/json on unsafe same-origin requests. Browser
cookies remain automatic and HttpOnly. Auth paths will need the development proxy;
trusted origins remain the browser-facing Vite host/port, not the upstream target.

### Validation and limitations

`test_sessions.py` and `test_csrf.py` cover random generation, hashed-only storage,
active/anonymous sessions, boundary expiry, renewal/capping, revocation and rotation,
transaction rollback/commit failures, sanitized exceptions, cookies/no-store, HMAC
binding, constant-time comparison, strict origins/Referer, duplicate headers, and
content types. They use SQLite or direct Request/Response objects and never change
PostgreSQL. PostgreSQL row-lock behavior is implemented but concurrent transactions
are not exercised by SQLite. Expired-row cleanup and endpoint-level throttling are
future work; expiry enforcement does not depend on cleanup. Reuse the existing
backend pytest command; no dependencies or schema changes are required here.
