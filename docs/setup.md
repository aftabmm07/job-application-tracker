
# Development Setup Guide

## Prerequisites

- Git
- Visual Studio Code
- Docker Desktop with WSL 2 integration enabled
- Ubuntu WSL
- CPython 3.14 with pip and venv support (CI and this lock validated on 3.14.4)

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
python -m pip install --require-hashes --only-binary=:all: -r requirements-bootstrap.txt
python -m pip install --require-hashes --only-binary=:all: -r requirements.txt
python -m pip check
```

Dependencies include FastAPI, Uvicorn, Pytest, HTTPX, SQLAlchemy, Psycopg, and python-dotenv.

`requirements.in` contains exact direct runtime/test pins. `requirements.txt` is
the generated pip-tools lock for all 41 direct/transitive packages, with SHA-256
artifact hashes. `requirements-bootstrap.txt` pins the universal pip 26.2 wheel
with its hash. Local setup and GitHub Actions use these same three commands;
neither performs a floating pip upgrade or resolves fresh application versions.
Wheel-only installation prevents build backends from installing unlocked source-
build dependencies. Use a clean virtual environment when moving to a new lock:
pip installation does not remove unrelated packages already present.

The supported lock target is standard CPython 3.14 on Linux/WSL; validation used
3.14.4 on x86_64. It is not a native-Windows, macOS, or free-threaded Python lock.
Although hashes cover multiple published artifacts, platform-dependent dependency
selection was resolved on Linux. Do not remove wheel-only or hash checks to work
around an unsupported platform; review and generate a separate lock instead.

### Intentional dependency refresh and audit

Do not edit generated application pins/hashes by hand. Use a separate maintainer
environment so pip-tools and pip-audit do not pollute the backend test environment.
From `backend/`, create it with the documented Python 3.14 interpreter:

```bash
python3 -m venv ~/python-envs/job-tracker-lock-tools
~/python-envs/job-tracker-lock-tools/bin/python -m pip install --require-hashes --only-binary=:all: -r requirements-bootstrap.txt
~/python-envs/job-tracker-lock-tools/bin/python -m pip install pip-tools==7.6.2 pip-audit==2.10.1
~/python-envs/job-tracker-lock-tools/bin/python -m piptools compile --generate-hashes --reuse-hashes --no-emit-index-url --no-emit-trusted-host --allow-unsafe --strip-extras --output-file=requirements.txt requirements.in
```

The compile command reuses existing exact pins rather than broadly upgrading.
To update a direct dependency, review its release/security notes, change that exact
pin in requirements.in, then compile. To intentionally update a transitive package,
use `--upgrade-package PACKAGE==REVIEWED_VERSION` with the compile command. Avoid
an unrestricted `--upgrade`. Review the entire resulting diff, install the new lock
in a clean backend environment, run pip check and the complete pytest suite, and
repeat the advisory audit. Lock generation/downloads require network access; never
embed private index credentials or machine paths in committed files.

For a bootstrap update, download the reviewed universal pip wheel separately,
calculate its SHA-256 with `python -m pip hash WHEEL`, compare it with the release's
PyPI digest, and update both the exact pin and hash in requirements-bootstrap.txt.
Regenerate and validate the application lock using that installer, then repeat the
clean installation, audit and tests. Do not replace this with a floating pip upgrade.

Audit the complete app and installer locks without modifying them:

```bash
~/python-envs/job-tracker-lock-tools/bin/python -m pip_audit --disable-pip --strict -r requirements.txt -r requirements-bootstrap.txt --format json --output dependency-audit.json
```

This sends package names/versions to the default PyPI advisory service, not source
code or secrets. Store the report as a review/CI artifact rather than committing
it as a permanent security claim. Maintainer tools have explicitly selected versions
in the command above, but their separate transitive environment is not part of the
application lock. Hashing pins artifacts; it does not prove package safety.

On 2026-10-16, pip-audit 2.10.1 reported no known vulnerabilities or skipped packages
for the 42 locked application/installer packages. Auditing the previous pip 25.1.1
reported six distinct advisories (some duplicated by the service): CVE-2025-8869,
CVE-2026-1703, CVE-2026-3219, CVE-2026-6357, CVE-2026-8643 and CVE-2026-13346.
The exact pip 26.2 bootstrap addresses the reported fixed-version requirements.
Application versions were retained from the validated baseline. Advisory scanning
is time-dependent, is not a reachability assessment, and does not cover unknown
vulnerabilities, OS libraries, container images, or all tooling dependencies.

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

Only after approval to use an isolated, migrated development database, select its
name explicitly. Do not use the original database or preserved rehearsal databases
without separate approval. The name below is an example approved development target,
not permission to change it:

```bash
APP_ENV=development APP_DATABASE_NAME=jobtracker_auth_fresh_9b2 python -m uvicorn app.main:app --reload
```

Startup fails before engine creation when `APP_DATABASE_NAME` is missing, empty,
invalid, or forbidden. There is no runtime default in development or production.

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
| `PASSWORD_HASH_MAX_CONCURRENCY` | `2` | Optional integer 1–8. Maximum concurrent password operations per process; restart after changing. |
| `JSON_MAX_BODY_BYTES` | `65536` (64 KiB) | Optional integer 1–1048576. Maximum mutation body size in bytes, before JSON parsing; restart after changing. |

`POSTGRES_PASSWORD` remains required by the existing database configuration.
`APP_DATABASE_NAME` is also required in every environment, with no fallback. Use
1–63 lowercase ASCII letters, digits or underscores, starting with a letter. Names
are not trimmed or truncated. `jobtracker_db`, `postgres`, `template0` and
`template1` are forbidden before engine creation. Exported values take precedence
over the private project-root `.env`. Production must explicitly configure its
intended migrated database; no development target is selected automatically.
These checks validate configuration, not migration state or permission to use a DB.
`python-dotenv==1.2.4` is now explicitly declared, alongside `pwdlib[argon2]==0.3.1`.

### Isolated disposable MVP demo database

The operator-approved current demo run has been started and its Docker metadata
reports a healthy container, dedicated volume/network, and loopback-only port.
No migration has been applied or database connection made for this run. This is
a status report, not approval to reuse its identity or provision another run.
Future runs require a new reviewed run ID and separate approval.

`compose.demo-db.yaml` is a **separate, opt-in** PostgreSQL 17 service. It is
not part of `compose.yaml` or the mail-capture file. Its project is
`jobtracker-demo-<run-id>`, database `jobtracker_demo_<run-id>`, role
`jobtracker_demo`, project-scoped named volume `demo_pgdata`, and host binding
`127.0.0.1:55434:5432`. `<run-id>` must be a fresh 16-character lowercase
hex string. No default run ID, password, database name, or approval is supplied.
The existing `jobtracker_db`, `job-tracker-db`, and `postgres_data` must not be
used. Do not combine Compose files with `-f compose.yaml`.

Backend and Alembic select this target only when `APP_DATABASE_MODE=demo` and
`APP_ENV=development` are explicit. `DEMO_RUN_ID` determines the exact database
name; `DEMO_POSTGRES_PASSWORD` is a separate private value of at least 16
characters. Fixed connection settings are `127.0.0.1:55434` and role
`jobtracker_demo`. Ordinary target and custom host/port/user variables conflict
with demo mode. Production rejects demo mode. Demo startup and migration loading
skip the ordinary project `.env`; export demo settings only in the approved
process environment. Do not print environment dumps, database URLs, unrestricted
`docker compose config`, or Docker inspection output because they can contain
passwords. The older `APP_DATABASE_MODE=disposable` path remains blocked.

**Future operations require separate approval. Do not rerun creation for the
already-started current run.** In a private shell with tracing disabled, an
operator preparing a future run would generate a fresh `DEMO_RUN_ID` with a
cryptographic random generator, privately set a
different `DEMO_POSTGRES_PASSWORD`, and export `APP_ENV=development` and
`APP_DATABASE_MODE=demo`. Keep `APP_DATABASE_NAME`, `POSTGRES_PASSWORD`,
`MIGRATION_DATABASE_HOST`, `MIGRATION_DATABASE_PORT`, and
`MIGRATION_DATABASE_USER` unset. For migrations only, set
`MIGRATION_DATABASE_NAME=jobtracker_demo_<run-id>` using the *same* run ID.

Before creation, read-only inspections must establish that the intended Compose
project, its generated container/network/volume names, and host port `55434`
are unused. Stop on any collision; never adopt or replace an existing resource.
The following reusable commands are proposals for a future run, not approval
to execute them. Run them from the project root with the private demo variables
already exported. These checks are read-only and must produce no existing demo resource or
port listener. The Python check validates configuration without printing it or
opening a database connection:

```bash
PYTHONPATH=backend python -c 'import os; from app.database_config import demo_database_target; demo_database_target(os.environ)'
ss -ltn '( sport = :55434 )'
docker ps -a --filter "label=com.docker.compose.project=jobtracker-demo-${DEMO_RUN_ID}" --format '{{.ID}}'
docker volume ls --filter "name=jobtracker-demo-${DEMO_RUN_ID}_demo_pgdata" --format '{{.Name}}'
docker network ls --filter "name=jobtracker-demo-${DEMO_RUN_ID}_default" --format '{{.ID}}'
```

For a future run only, the next commands validate Compose configuration and
create its container, network and volume. Creation needs explicit approval;
the current run must not be created again:

```bash
docker --context default compose -f compose.demo-db.yaml -p "jobtracker-demo-${DEMO_RUN_ID}" config --quiet
docker --context default compose -f compose.demo-db.yaml -p "jobtracker-demo-${DEMO_RUN_ID}" up -d --no-deps --pull never demo_db
```

Only after verifying the exact resources for the selected run, use the guarded, read-only
`alembic current` connection to confirm the exact database identity. The
subsequent fresh-database migration is separately approved and state-changing:

```bash
cd backend
python -m alembic current
python -m alembic upgrade head
python -m alembic current
```

Alembic refuses an absent or mismatched `MIGRATION_DATABASE_NAME` and validates
the connected database name before schema changes. `upgrade head` creates the
versioned schema. The current demo run remains unmigrated.
A later approved backend start must use the same `DEMO_RUN_ID` and demo password.
Docker health and metadata were checked; migration and API behavior remain unverified.

Cleanup has two distinct stages. `docker --context default compose -f compose.demo-db.yaml -p "jobtracker-demo-${DEMO_RUN_ID}" ps`
is read-only inspection. `docker --context default compose -f compose.demo-db.yaml -p "jobtracker-demo-${DEMO_RUN_ID}" down` stops
and removes only the verified demo project's container and network; it is a
state-changing operation requiring separate approval. The named volume remains.
`docker --context default compose -f compose.demo-db.yaml -p "jobtracker-demo-${DEMO_RUN_ID}" down -v` **deletes demo database data**;
use it only with separate explicit deletion approval after checking the exact
run-specific project and immutable resource identity. Never run cleanup with an
old/reused run ID or against the original Compose file. Port publication and
Compose identity require live verification for each run before any migration.

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

Security fix 61.2D compares dialect-compiled SQL types instead of Python inheritance:
INTEGER accepts SQLAlchemy/PostgreSQL INTEGER representations, but BIGINT, SMALLINT,
fixed CHAR, unbounded/differently sized VARCHAR, timezone loss and timestamp precision
loss are rejected. PostgreSQL timestamp precision six is equivalent to its implicit
precision and is accepted. SQLite's reflected DATETIME remains supported for tests;
SQLite cannot establish PostgreSQL timezone behavior. SQLite's ID must be declared
exactly INTEGER: INT PRIMARY KEY does not create the same rowid alias.

Computed/identity columns are not the original serial baseline. The serial default
must be the complete nextval expression for job_applications_id_seq in public,
accepting legitimate qualification/quoting, with reflected autoincrement enabled.
Suffix expressions and other sequences/schemas are rejected. PostgreSQL materialized
views, user triggers, partitioned/unlogged tables and enabled/forced row security
also differ from the baseline and block adoption.

An existing 0001_legacy version is reverified before upgrade because schema drift
can occur after stamping. Planned stamp steps are checked before writing a version
row: only explicit 0001_legacy adoption of a verified existing schema is allowed.
Empty databases must run upgrade, and stamp head cannot skip authentication DDL.
Failed SQLite stamp attempts may leave an empty Alembic version table (SQLite DDL
is not fully transactional); they must never leave an unverified version row.
The historical 0001/0002 migration scripts and data remain unchanged.

New tests exercise populated SQLite preservation on failed adoption and compatible
baseline upgrades, plus reflected PostgreSQL types/defaults/options with mocks.
No live PostgreSQL adoption or DDL was executed for this fix. CI's existing fresh
PostgreSQL migration/check and sequential ownership coverage remain, but a newly
approved disposable PostgreSQL legacy clone is still needed to validate real
catalog reflection, serial defaults and populated adoption. Never use the original,
restore-test, or preserved rehearsal databases for that validation.

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

At checkpoint 9B.3 these helpers were not attached to endpoints. Checkpoint 9B.4
below adds the auth API while keeping health and application CRUD behavior unchanged.
The helper checkpoint itself introduced no endpoints or PostgreSQL changes.

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
- `renew(raw_token)` locks the session before sampling fresh UTC time, then
  conditionally updates last activity and idle expiry only while the stored row
  remains valid. Idle expiry is capped by the original absolute
  deadline. It cannot recreate a revoked row, revive an expired session, or move
  activity backwards when an older request arrives late.
- `revoke(raw_token)` locks and rechecks the live session before deleting it;
  missing, expired, disabled-user or already-revoked sessions return False.
  Repeated revocation is harmless; expired rows belong to explicit cleanup.
- `rotate_for_login(anonymous_token, user_id)` requires a live anonymous session,
  locks it on PostgreSQL, issues a fresh authenticated token, and removes the old
  row. Both changes belong to one transaction. Existing authenticated sessions are
  not accepted as anonymous login sessions. Repeated consumption is rejected.
- `cleanup_expired(batch_size=100)` is explicit maintenance, never part of a
  normal request. It removes at most one batch of absolutely or idle-expired
  sessions; see the operational guidance below. It does not commit automatically.

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
are not exercised by SQLite. Endpoint throttling and explicit expired-session
cleanup were added in later checkpoints; expiry enforcement does not depend on
cleanup. Reuse the existing backend pytest command.

## Authentication API (Phase 14, checkpoint 9B.4)

Checkpoint 9B.4 added backend auth endpoints. Checkpoint 9B.5 below now protects
application CRUD; the frontend still needs Phase 15 authentication integration.
**Do not deploy publicly yet**, and never run this branch on the original unmigrated
`jobtracker_db`. No email is sent, and production account activation is not provided.

### Safe local target and test workflow

Run automated tests from `backend/` with `python -m pytest -q`. Auth integration
tests override the shared database dependency with in-memory SQLite, use synthetic
credentials, and never connect to PostgreSQL. They cover the complete flow below,
including the private activation helper with an explicitly allowed test target.

For manual HTTP testing, use only a separately approved migrated development DB.
`APP_DATABASE_NAME` must explicitly select the API database; there is no legacy
default, and `jobtracker_db` is rejected before engine creation. The existing
private POSTGRES_PASSWORD/user/host settings remain unchanged.
For example, AFTER approval to write the isolated target, from `backend/`:

```bash
APP_ENV=development APP_DATABASE_NAME=jobtracker_auth_fresh_9b2 python -m uvicorn app.main:app --reload
```

Starting a server and using registration/login/bootstrap will write that selected
DB. Do not run this command or issue database-changing HTTP requests without the
required approval. The frontend proxy does not yet include /auth; test directly
against the backend with an HTTP client/cookie jar and the trusted development
Origin header, or use the existing automated TestClient suite.

### Endpoint flow and HTTP contract

1. **GET /auth/csrf** with a trusted Origin (for example http://localhost:5173), or
   a strict trusted Referer fallback. It returns `{"csrf_token":"..."}` and no-store.
   Without a valid cookie it commits a fresh anonymous session and then sets the
   HttpOnly cookie. With an existing valid anonymous/authenticated session it
   returns its CSRF token without setting another cookie, creating another row,
   rotating it, or extending activity. Expired/unknown cookies get fresh random
   anonymous sessions. Expired rows await explicit scheduled cleanup. Raw bearer tokens
   never appear in JSON. Keep the cookie jar and returned CSRF token.
2. **POST /auth/register** with application/json, Origin/Referer, the cookie and
   X-CSRF-Token. JSON is `{"email":"person@example.com","password":"..."}`. Email
   syntax is checked with email-validator (no DNS/network checks), normalized and
   lowercased; display names are rejected. Password policy is unchanged. Extra
   fields such as is_active/user_id are rejected. Both new and duplicate addresses
   receive the same generic 202 response. Both paths perform Argon2id hashing.
   A database uniqueness race is rolled back and confirmed against normalized email
   before returning the generic response; unrelated constraint failures return 503.
   Duplicate registration never changes the stored password. New users remain
   inactive/unverified, and registration creates no authenticated session/cookie.
3. **Development activation**, described below, is required to test successful login.
   Public email delivery/verification remains deferred; a normal new registration
   cannot sign in in production. No response claims that an email has been sent.
4. **POST /auth/login** uses the same JSON fields and anonymous cookie/CSRF headers.
   Wrong passwords, unknown accounts and inactive/unverified accounts all receive
   401 `Invalid credentials`. Unknown accounts use a precomputed Argon2id dummy
   hash so they still perform password verification. Correct credentials rotate the
   live anonymous session to a fresh authenticated token, deleting the old session
   in the same transaction. Any password-hash upgrade is part of that transaction.
   Only after commit does the new HttpOnly cookie get set. JSON contains only id
   and normalized email. An already authenticated cookie is not accepted as an
   anonymous login session (403); logout/bootstrap before signing in again.
5. **GET /auth/me** requires the authenticated cookie and returns only id/email.
   Missing, anonymous, expired, revoked or disabled-account sessions return 401.
   A successful request renews idle activity without extending absolute expiry.
   The reusable `get_current_user` dependency performs read-only authentication;
   future unsafe CRUD handlers must validate CSRF/ownership before renewal/mutation.
6. After login, fetch **GET /auth/csrf** again: rotation changed the CSRF token.
   **POST /auth/logout** requires the current cookie, Origin/Referer,
   application/json (even without a body) and X-CSRF-Token. It revokes and commits
   before clearing the cookie, returning empty 204. A missing/invalid/expired or
   replayed session returns generic 403 and changes no session/cookie; bootstrap
   again when needed. Valid anonymous sessions may also be explicitly logged out.

All /auth responses, including validation failures, unknown auth paths and method
errors, have Cache-Control: no-store. Unsupported content types return 415;
missing/invalid CSRF or browser source returns 403. Invalid request schemas/JSON
return generic 422 without raw input, password, validation ctx or parser details.
Storage failures return generic 503 and roll back. Unexpected auth failures return
sanitized 500 without logging the exception or submitted input. Credential models
use SecretStr and hide_input_in_errors; route-level handling is also necessary
because FastAPI validation error dictionaries can otherwise contain raw inputs.
Do not configure request-body/cookie/header logging in proxies or APM tools.

### Private development activation

There is NO activation route. `scripts.activate_development_user` is an operator-only
CLI, not imported or called by endpoints. It refuses production configuration,
the original database, arbitrary DB names, and missing explicit confirmation before
DB access. It permits only `jobtracker_auth_fresh_9b2`, `jobtracker_auth_legacy_9b2`,
and disposable `jobtracker_auth_ci`; actual PostgreSQL identity is checked too.

Only after explicit approval, for an independently confirmed disposable test user:

```bash
APP_ENV=development MIGRATION_DATABASE_NAME=jobtracker_auth_fresh_9b2 python -m scripts.activate_development_user --user-id USER_ID --confirm-development-activation
```

The command sets email_verified_at and is_active transactionally. It does NOT
verify a real mailbox. Find the intended test user ID through controlled operator
access; registration intentionally does not return IDs or existence information.
Never give production credentials to this tool or mislabel production as development.
Adding future targets requires explicit review of its allowlist. Production needs
an actual expiring, single-use mailbox verification process before public activation;
it must bind verification and credential setup to the verified registrant and avoid
activating an attacker-chosen password from an earlier pending registration.

### Throttling and deployment limitations

The process-local, bounded, thread-safe limiter allows:

- CSRF bootstrap: 60 requests per IP per minute (reused sessions included).
- Registration: 10 requests per IP per 15 minutes, before hashing.
- Login: 30 requests per IP and 10 per normalized email per five minutes, before
  password verification. Attempts are counted regardless of account existence or
  outcome. 429 includes Retry-After and no-store. Keys are hashed in memory; the
  limiter stores no plaintext email, password or session token.

Windows reset on expiry/process restart. Limits are per worker; multiple workers
require a shared limiter (for example at the trusted reverse proxy or a shared
store), distributed abuse monitoring, and request body/connection limits. Temporary
email-based throttling can be abused to delay another user's login, so tune limits
and broader abuse controls for production. Proxy IP forwarding must be configured
only for trusted proxies; never trust arbitrary X-Forwarded-For. CORS stays closed.
Generic responses and comparable hashing reduce enumeration signals but do not
promise identical latency, particularly across inserts, contention, and hash upgrades.

### Authentication resource limits (security fix 61.2B)

All password hashing and verification helpers share one process-local, nonblocking
gate, including dummy verification for unknown accounts and verification that
upgrades a hash. The default allows two operations, approximately 128 MiB of Argon2
working memory plus application overhead. Argon2id remains at 64 MiB per operation,
three passes and parallelism four. The gate does not queue: when full, auth endpoints
return generic 503 with Retry-After: 1 and Cache-Control: no-store. Existing attempt
throttling still returns 429 independently. Capacity rejection does not create a
user, rotate a session, or issue a cookie.

Auth endpoints remain synchronous FastAPI worker functions, so password work does
not execute on the event loop. A finally block releases each permit after success,
failure or cancellation unwinds the actual operation. Cancellation of a caller
waiting for a worker does not interrupt Argon2 or release the worker's permit early;
the running operation retains capacity until it finishes. The hash-upgrade path
holds one permit throughout verification and replacement hashing.

The ASGI request-body middleware limits POST, PUT, PATCH and DELETE bodies across
the API, including registration, login, logout and application mutations. It rejects
an oversized Content-Length immediately and counts actual incoming bytes even when
the header is missing or understated, or the upload is chunked. It stops receiving
at the first excess chunk and returns generic 413 with no-store, before routing,
JSON parsing or database dependencies. Invalid/duplicate Content-Length headers
receive generic 400. HTTP/1 rejection responses request connection closure rather
than draining the upload; HTTP/2 responses omit the Connection header.

Accepted bodies are buffered only up to JSON_MAX_BODY_BYTES and replayed to the
existing handlers. This is a whole JSON-body byte limit, not a character limit;
JSON syntax and multibyte Unicode count toward it. With the default 64 KiB limit,
very large job descriptions must be shortened. Small JSON requests and existing
CSRF, authentication and ownership checks retain their behavior. Safe-method and
non-HTTP traffic bypass this mutation-body middleware.

These are per-process safeguards, not complete deployment resource controls. N
workers can run N times the configured password concurrency. Choose workers and
capacity together after benchmarking CPU, memory and latency; keep shared abuse
throttling at a trusted proxy or store. The hashing gate does not bound requests
waiting for general worker threads or database connections. A server may already
have allocated an incoming ASGI chunk, and many simultaneous bounded uploads can
still consume memory. Production therefore also needs proxy body limits at or
below the application limit, upload/header timeouts, connection/concurrency limits,
and overload monitoring. No in-process body-size limit prevents slow uploads.
Reject unsupported request compression at the proxy; this middleware does not
decompress request bodies. Never log body contents, cookies, CSRF tokens or secrets.

`test_password_limits.py` covers saturation, shared capacity, real Argon2 behavior,
failure/cancellation release and cancellation while a worker remains active.
`test_request_limits.py` supplies ASGI streaming chunks directly to verify bounded
reads and pre-handler rejection. Auth integration tests cover generic overload
responses, dummy verification, worker-thread execution, early body rejection and
small chunked registration; the existing full suite covers CRUD and security
regressions. These tests use SQLite overrides and require no PostgreSQL changes.

At checkpoint 9B.4, application ownership was not enforced; see 9B.5 below. No PostgreSQL concurrency or
mailbox-verification delivery is exercised by the SQLite suite. No PostgreSQL
commands, server startup, migrations, activations or real registration were executed
as part of this checkpoint's validation. email-validator==2.3.0 is now a dependency.

## Application ownership and authorization (Phase 14, checkpoint 9B.5)

Every `/applications` endpoint requires an authenticated, active, email-verified
user through the existing get_current_user dependency. Anonymous, expired, revoked,
disabled, and invalid sessions receive 401. The welcome and health endpoints remain
public. There is no separate backend analytics endpoint; analytics use the scoped
GET list, so one user's charts cannot include another user's or legacy records.

- POST derives user_id only from the authenticated user. Create and PATCH schemas
  reject user_id and all other undeclared fields with 422. Response fields remain
  unchanged and never include ownership IDs, password hashes, or session data.
- GET list filters by owner and retains descending ID order.
- GET detail filters by both ID and owner. PATCH and DELETE include the same pair
  of conditions directly in UPDATE/DELETE SQL and return only matching rows.
  Empty PATCH is an owner-scoped read/no-op. Nullable-field clearing still works.
- Foreign, nonexistent, and NULL-owned IDs share the exact same 404 body. There is
  no public legacy-claim operation. No legacy owner is assigned automatically.
- Unsafe requests require the existing CSRF token, trusted Origin/strict Referer,
  and JSON Content-Type. This includes bodyless DELETE, consistent with the existing
  CSRF helper. Failed checks do not renew activity or change application rows.
- Successful CRUD operations, including reads, renew idle activity without changing
  absolute expiry. Renewal and mutations share a commit. If final session renewal
  fails because the session was revoked, expired or became ineligible, pending
  application changes roll back with 401. Database failures roll back with generic
  503 responses. Public response snapshots are created before commit; there is no
  unscoped post-commit application refresh. All application responses use no-store.

Legacy rows with NULL user_id are now quarantined by the application queries. This
checkpoint does not update, delete, reassign or migrate any existing PostgreSQL
rows. The original database remains unmigrated and must not be used by this branch.
The backup, restore-test DB, and earlier rehearsal DBs must remain unchanged.

### Tests and CI safety

The original CRUD regression assertions remain, with fixtures now obtaining real
anonymous sessions, logging in active SQLite test users, and sending the rotated
session's CSRF token. Authentication is not bypassed in those fixtures. Ownership
integration tests exercise two real logged-in clients, guessed/legacy IDs, scoped
lists, mutation SQL predicates, ownership injection, session failures, rollback,
content types, CSRF and origin controls. No PostgreSQL-changing command is needed
for the default full suite: run `python -m pytest -q` from `backend/`.

An optional PostgreSQL API check exists in `test_postgres_ownership.py`. It is skipped
unless RUN_POSTGRES_OWNERSHIP_TESTS=1 and refuses any target other than
`jobtracker_auth_ci` before connecting. CI runs it AFTER upgrading/checking the fresh
PostgreSQL 17 service. It inserts only synthetic CI rows in an outer transaction,
uses savepoints for endpoint commits, rolls everything back, and verifies zero rows
remain. It refuses a nonempty CI database. Do not run its opt-in command locally
without explicit database-changing approval; it does not target any restored or
rehearsal database. CI definitions are not evidence of a remote run in this task.

### Concurrency and remaining production work

Owner predicates are part of each mutation statement, avoiding a read-then-authorize
race. Concurrent deletion or ownership changes before an UPDATE/DELETE predicate
is evaluated cannot turn it into an unscoped mutation. PostgreSQL serializes row
updates; disjoint partial updates do not overwrite omitted fields. There is no
optimistic version column: concurrent edits to the same field remain last-write-wins.

Session renewal locks the row and checks fresh UTC time before applying its update.
A logout
that wins before renewal causes rollback; a logout waiting on renewal's row lock
can complete after that already-authorized request commits. Account deactivation
concurrent with an in-flight request is not a global cancellation mechanism: a
request authorized before deactivation may complete. SQLite tests cannot prove
PostgreSQL concurrent scheduling behavior; the opt-in CI check is sequential.
RLS is not enabled: SQL ownership filters protect API access, not direct privileged
SQL or future code that omits them. Continue reviewing new data endpoints.

Before production: verified-email activation, shared multi-worker throttling,
HTTPS/proxy and secret-management review, dependency/logging review, request size
limits, operational monitoring, and separately approved original-data migration and
verified ownership backfill are still required. Do not publicly deploy this branch.

### Required Phase 15 frontend changes

1. Add /auth to the existing Vite proxy, retaining HTML/API distinction for
   /applications. Bootstrap authentication with /auth/me before fetching private
   application data; treat 401 as signed out, not a generic retry loop.
2. Fetch /auth/csrf with the same-origin cookie, keep its token in memory, and use
   application/json plus X-CSRF-Token on POST/PATCH/DELETE and auth mutations.
   Fetch a new CSRF token after login rotation; never use localStorage for sessions.
3. Supply login/logout and registration/activation-required UI. Public registration
   does not activate users or send verification mail yet.
4. On logout, 401, or account changes, abort stale requests and clear application
   lists, analytics and account-specific state so one user's cached data cannot
   appear for the next user. Handle CSRF 403 separately and bootstrap/retry only
   when appropriate; never blindly replay a mutation that may have committed.
5. Continue deriving summaries and charts from the full owner-scoped API list.
   The current frontend was deliberately not modified and is not yet auth-ready.

## Strict session expiration and explicit cleanup (security fix 61.2C)

### Fresh time after locking

Renewal (`/auth/me` and successful application CRUD), login rotation, and logout
revocation now acquire the session lock before sampling UTC wall-clock time.
PostgreSQL uses SELECT FOR UPDATE with a refreshed ORM row. A waiter sees the
committed replacement row, or no row after concurrent deletion/rotation; cached
identity-map data is not used to authorize the operation. Both absolute and idle
expiry are enforced, with equality treated as expired. Conditional mutation SQL
retains the live-session and active/verified-user predicates. Login rotation also
requires an anonymous session; it cannot consume an authenticated session.

Lookup remains read-only and rechecks time after SQL returns, so a stalled read
cannot return an already-expired session. Login rotation issues and deletes within
one transaction. Absolute expiry is never extended by renewal. Logout refuses to
clear a cookie when the final live-session revocation fails. Existing commit-before-
cookie ordering, CSRF, ownership filters and error sanitization remain in place.

The authorization decision occurs using fresh time while holding the session
lock. It is not a guarantee that commit or response delivery occurs before the
deadline: an already-authorized transaction may finish later. Locks remain held
until commit/rollback, so subsequent renewals, revocation and cleanup serialize
against that decision. Account deactivation is still not cancellation of all
in-flight requests. Keep application clocks synchronized across hosts; PostgreSQL
transaction-start now() is deliberately not used for these decisions.

Before session writes/cleanup, PostgreSQL transaction-local lock_timeout is 2 s
and statement_timeout is 5 s. These fixed baseline limits apply to subsequent
statements in that transaction and reset on commit/rollback. Timeout/deadlock or
other SQLAlchemy failures roll back the complete unit of work and produce the
existing sanitized storage error (503 at HTTP boundaries). They do not bound total
transaction lifetime, connection-pool waits, or unrelated statements executed
before entering the session write operation. Review them with deployment workload
requirements rather than disabling them to hide contention.

SQLite has no row-level FOR UPDATE or SKIP LOCKED. Before session writes or cleanup,
the service sets the connection's busy_timeout to 2 s and executes a no-row UPDATE
to acquire the writer reservation before sampling time. This changes no records.
An incompatible read-to-write upgrade can fail with SQLITE_BUSY rather than wait;
it fails closed through rollback. SQLite serializes writers for the whole database,
so its successful concurrency tests do not prove PostgreSQL row-lock scheduling.

### Cleanup operation and scheduling

`SessionService.cleanup_expired(batch_size=100)` accepts an integer batch size
from 1 through 1000. It selects only sessions whose absolute OR idle deadline is
at or before the current time, locks at most the requested number of candidates,
then rechecks expiry with fresh time in the DELETE predicate. PostgreSQL uses
FOR UPDATE SKIP LOCKED to leave busy rows for a later run. Cleanup cannot delete
a row renewed before it acquires that row's lock. If cleanup deletes first, the
waiting renewal finds no row and cannot recreate it. A backwards clock change
between selection and deletion is handled conservatively by the second check.

The existing absolute/idle expiration indexes support candidate selection; no
index or schema changes and no migration are needed. Batch size bounds deletions
and row locks, not the planner's scanned rows; statement_timeout bounds long SQL.
No users, applications, NULL-owned legacy records or backup data are selected or
deleted. Inactive but unexpired sessions are retained until their time deadline.
Expired authenticated and anonymous rows follow the same cleanup rules.

Cleanup is a service function, not a public endpoint, startup hook, or automatic
background loop. An operator-approved scheduler wrapper can call it as follows,
using an explicitly approved migrated database engine and existing auth settings:

```python
from sqlalchemy.orm import Session
from app.session_service import SessionService, commit_session_changes

# approved_engine and settings must be supplied by reviewed operator configuration.
with Session(approved_engine, autoflush=False) as db:
    removed = SessionService(db, settings).cleanup_expired(batch_size=100)
    commit_session_changes(db)
# Log only removed (a count), never session rows, tokens or database URLs.
```

Obtain approval for the exact target and wrapper command before executing against
PostgreSQL. Never target jobtracker_db, the restored backup database, or preserved
rehearsal databases. There is intentionally no default cleanup target or runnable
command that silently connects to one. This checkpoint has not run cleanup against
any PostgreSQL database.

For production, schedule a bounded job every 5 minutes (for example with a systemd
timer or CronJob). Start with one batch per invocation, monitor duration, failures
and the expired-row backlog, and tune batch size/frequency to traffic. If multiple
batches are necessary, impose a finite job budget and commit each batch using a
fresh dedicated Session; never loop indefinitely in one transaction. Always close
or roll back failed sessions promptly. Skipped/busy rows may make a batch smaller
than requested; a small or empty batch does not prove the entire backlog is empty.
Repeated runs are idempotent and active sessions do not require cleanup to expire
correctly. Provision only the permissions needed by the reviewed maintenance role.

### Validation and PostgreSQL gaps

SQLite tests cover exact boundaries, post-lock clocks, stale read results,
revocation/renewal ordering, rotation token invalidation, bounded/idempotent cleanup,
active-session and application preservation, rollback, and independent waiting
writers. PostgreSQL SQL compilation/mocks verify FOR UPDATE, SKIP LOCKED and local
timeout configuration without opening a PostgreSQL connection.

The existing opt-in PostgreSQL ownership check remains sequential. Before production,
rehearse independent PostgreSQL connections on a newly approved disposable database:
hold a session lock across idle/absolute expiry; race login rotation consumers;
race renewal with logout and cleanup in both commit orders; verify SKIP LOCKED
returns promptly; and verify lock/statement timeout rollback. Run under the intended
READ COMMITTED isolation and test configured alternatives separately. Do not infer
those PostgreSQL guarantees solely from SQLite tests or SQL compilation. No such
PostgreSQL write/rehearsal has been performed in this checkpoint.
