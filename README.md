# Job Application Tracker

A full-stack application for organizing a private job search: track applications,
review progress, and manage your profile in a React interface backed by FastAPI
and PostgreSQL.

**Status:** The local MVP is implemented. It is not publicly deployed.

## What the application does

- **Accounts:** Register, verify email, request a new link, sign in, and sign out.
- **Applications:** Create, inspect, edit, and delete records with a confirmation
  step. Track company, role, location, status, date, and job description.
- **Search and filter:** Find applications by company or role and narrow them by
  status. Use a desktop table or mobile cards.
- **Dashboard:** Review totals, status and monthly charts, plus interview and
  offer indicators based on current records.
- **Profile:** Manage your name, headline, location, and HTTPS professional links;
  see your verified email as read-only.

Application queries and changes are scoped to the signed-in user. Registration
and resend use generic responses, and verification does not sign the user in.
The UI handles loading, empty, and error states and hides private data when a
session cannot be confirmed.

## Technology

| Layer | Implementation |
| --- | --- |
| Frontend | React, Vite, React Router, Recharts; responsive CSS |
| Backend | Python, FastAPI, Pydantic, SQLAlchemy |
| Database | PostgreSQL 17 with Alembic migrations |
| Local containers | Docker Compose; Nginx serves the built frontend and proxies API requests |
| Local email testing | Optional Mailpit capture; no paid email service required |
| Automation | GitHub Actions, Pytest, Node test runner, Oxlint, Playwright source tests |

Email verification uses expiring bearer links delivered through a configurable
sender. The local capture mode is development-only; production requires a
separately configured SMTP sender and HTTPS verification origin. The advanced
transactional email outbox is present as dormant source and is not active.

## Getting started safely

There are several database and Compose configurations in this repository.
Choose a fresh, explicitly reviewed local target before starting services or
running migrations. Do not reuse an existing project, volume, or database.

1. Read the [development setup guide](docs/setup.md) for the local Python,
   PostgreSQL, and Vite workflows.
2. For the isolated React/Nginx, FastAPI, and PostgreSQL stack, follow the
   [container operator runbook](docs/container-stack.md). It separates resource
   checks, private settings, migrations, service startup, and cleanup.
3. Use [frontend instructions](frontend/README.md) for frontend commands and
   browser behavior. The optional local mailbox contains sensitive links;
   follow the runbook's access and cleanup precautions.

The isolated container stack was used in a **recorded manual browser test** on
2026-10-29: registration, captured email verification, sign-in, and application
Create/Read/Update/Delete passed. The run's containers were stopped while its
database volume was preserved. This is one local observation, not a currently
running deployment, an automated browser result, or production validation.

## API and architecture

FastAPI exposes `/auth` routes for CSRF bootstrap, registration, verification,
resend, login, logout, identity, and profile; `/applications` routes provide
owner-scoped CRUD. `/health` and `/health/db` report service and database health.
The frontend uses same-origin API paths; Nginx routes API requests to FastAPI
and serves React routes from the built assets.

- [System context](docs/architecture/system-context.md)
- [Logical container view](docs/architecture/container-view.md)
- [Isolated Docker deployment design](docs/architecture/docker-deployment.md)
- [CI pipeline diagram](docs/architecture/ci-pipeline.md)

The diagrams describe source design. Consult the operator runbook and verify
live resource identities before interpreting any deployment status.

## Testing and CI

Two GitHub Actions workflows run on pull requests targeting `main` and pushes
to `main`:

- **Backend CI** installs hash-locked Python dependencies, runs Pytest, checks
  Alembic migrations against a fresh PostgreSQL 17 CI service, and runs the
  explicitly enabled ownership test.
- **Frontend CI** installs from the npm lockfile with `npm ci`, then runs the
  existing unit/DOM tests, lint, and production build.

Playwright source covers a homepage smoke check, registration through email
verification and sign-in, and application CRUD. Browser E2E tests require a
separately approved disposable environment; **they do not run in CI**. Neither
CI workflow builds a release image or deploys the application.

For test boundaries and operator steps, see the
[disposable E2E runbook](docs/playwright-e2e-infrastructure.md),
[PostgreSQL integration-test guide](docs/postgres-integration-tests.md), and
[frontend test guide](frontend/README.md).

## Scope and next ideas

The MVP is a private application tracker. Job URL import, job search automation,
job-description analysis, and CV matching are **planned ideas**, not implemented
features. Public deployment would also require separate review of HTTPS,
credentials, email delivery, shared rate limiting, and operational safeguards.
