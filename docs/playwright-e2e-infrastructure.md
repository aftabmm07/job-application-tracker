# Disposable Playwright environment — source only, not provisioned

`compose.e2e.yaml` is a separate development stack. Its database and mailbox are
disposable. This document does not authorize a Docker, database, or cleanup
operation. Never reuse run IDs `06479b7b1e0fc5d4` or
`d3a273dcf94789a9`, or combine this file with another Compose file.

## Approve identity and private settings

Approve a fresh 16-character lowercase hexadecimal `E2E_RUN_ID`, two distinct
unused loopback host ports in `20000–60999`, and the Docker context/daemon. The
project is `jobtracker-e2e-<run-id>`; its database is
`jobtracker_stack_<run-id>` with role `jobtracker_stack`. The dedicated volume
is `<project>_e2e_pgdata`; edge, private, and mail networks are project scoped.
PostgreSQL, FastAPI, and SMTP have no host publication. Only frontend and the
Mailpit UI bind to `127.0.0.1`. The private database network is internal. The
mail network permits the loopback-published UI and is not claimed to prevent
backend egress. This is HTTP development use only; never expose the ports
publicly. The transactional email outbox remains disabled.

In a fresh shell with tracing off, create a new private file outside the repo
at `~/.config/jobtracker-e2e/<run-id>.env`, directory mode `0700`, file mode
`0600`. It must contain exactly `E2E_RUN_ID`, `E2E_FRONTEND_PORT`,
`E2E_MAIL_UI_PORT`, `E2E_POSTGRES_PASSWORD`, and `E2E_CSRF_HMAC_SECRET`.
Generate independent random secrets of at least 32 printable ASCII characters;
never reuse an existing password or secret. Do not display, stage, commit, or
copy the file into this repository. Set only `E2E_RUN_ID` and `E2E_ENV_FILE` in
the operator shell. Leave `E2E_FRONTEND_PORT`, `E2E_MAIL_UI_PORT`,
`E2E_POSTGRES_PASSWORD`, and `E2E_CSRF_HMAC_SECRET` unset in the shell: Compose
uses shell variables before `--env-file` values. The preflight compares the
exported run ID with the private file and rejects these inherited overrides.
Also leave `COMPOSE_PROJECT_NAME`, `COMPOSE_FILE`,
`COMPOSE_PROFILES`, `STACK_RUN_ID`, `STACK_POSTGRES_PASSWORD`,
`STACK_CSRF_HMAC_SECRET`, `DATABASE_URL`, `POSTGRES_PASSWORD`, and `PG*`
database settings unset. The source-only preflight checks the private file
without printing values. This is an **offline** check; it cannot prove Docker
resource availability or database identity. Repeat it immediately before each
future Compose command, without changing the shell environment between check
and command:

```bash
python3 backend/scripts/e2e_preflight.py
```

## Separate approval gates for live use

1. **Read-only preflight.** Check the selected Docker context, Unix endpoint,
   and daemon identity. Confirm that no project container, volume, or network
   with the exact derived names exists. Check the two approved host ports in
   Docker, WSL, and Windows. An occupied port or existing resource is a stop
   condition, even if labels appear familiar. Repeat these checks immediately
   before each mutation. Do not print unrestricted Compose rendering or inspect
   output: they can contain secrets.
2. **Build and database creation, separately approved.** Use only
   `compose.e2e.yaml` with explicit `--context`, `--env-file`, and `-p`.
   Create the new `db` only after collision checks. Verify immutable container
   identity, project labels, new volume attachment, and healthy status. Do not
   adopt an existing container or volume.
3. **Migration, separately approved.** A separate **live read-only** database
   identity check is required before schema changes. Connect only to
   the newly verified database and check server-reported database, role,
   `public` schema, port 5432, and empty application tables. The existing
   `check_stack_target` guard and `MIGRATION_DATABASE_NAME` must agree with
   `jobtracker_stack_<run-id>`. Review the migration chain first, then run one
   guarded upgrade to `0005_user_profile`; no API startup runs migrations.
   Verify the resulting Alembic revision and expected schema before starting
   the API. Stop on any mismatch; do not reset or retry automatically.
4. **Services, separately approved.** Start the Mailpit `capture` service,
   then backend and frontend after their dependencies are healthy. Confirm
   browser and Mailpit UI respond only on the approved loopback ports. Set
   `PLAYWRIGHT_BASE_URL=http://localhost:<approved-frontend-port>` and
   `PLAYWRIGHT_MAILPIT_URL=http://localhost:<approved-mailpit-ui-port>` for the
   browser test, along with the matching `PLAYWRIGHT_E2E_RUN_ID`,
   `PLAYWRIGHT_E2E_FRONTEND_PORT`, and `PLAYWRIGHT_E2E_MAIL_UI_PORT`. Playwright
   rejects missing, reserved, or mismatched values. The backend's allowed Origin and verification-link origin are
   exactly that value. Mailpit SMTP remains internal at `capture:1025`.
   Captured mail contains bearer links; keep messages and Playwright output
   private. Do not log or export tokens, cookies, passwords, or email bodies.
5. **Testing.** Run `npm run test:e2e` from `frontend/` only after the approved
   environment is ready. The current browser suite is a homepage smoke test;
   registration and CRUD scenarios are future work. Offline helpers generate a
   fresh fictional account and select its Mailpit message by exact recipient,
   sender, and subject; they return only a message ID and do not fetch the
   bearer-bearing body. Future tests must handle links transiently and keep
   bearer URLs out of Playwright failures and artifacts. Only a small, allowlisted
   route label may be attached on failure; screenshot, video, and trace capture
   remain off.
6. **Shutdown and cleanup require separate approval.** First stop only the
   exact verified E2E project services. Stopping preserves the database volume.
   Review immutable resource identities before any later container or volume
   removal. Never use a broad `down -v`, remove an existing stack/demo volume,
   or treat source rollback as database recovery. Preserve failed-run resources
   for diagnosis until cleanup is explicitly approved.

The following are **review templates, not instructions to execute now**. Set
`E2E_RUN_ID` and `E2E_ENV_FILE` as above, rerun the private settings check, and
review the exact Docker context, identities, image digest, and ports first.
For an unoccupied new run, a separately approved operator can use:

```bash
docker context show
docker context inspect default --format '{{.Endpoints.docker.Host}}'
docker --context default info --format '{{.ID}}'
docker --context default ps -a --filter "label=com.docker.compose.project=jobtracker-e2e-${E2E_RUN_ID}" --format '{{.ID}} {{.Names}}'
docker --context default volume ls --filter "name=jobtracker-e2e-${E2E_RUN_ID}_e2e_pgdata" --format '{{.Name}}'
docker --context default network ls --filter "name=jobtracker-e2e-${E2E_RUN_ID}_" --format '{{.Name}}'
```

An empty project resource listing is required. Check the two approved ports in
both WSL and Windows before every startup; do not rely solely on Docker's
published-port listing. Stop if the selected context, endpoint, daemon,
resource names, or listeners differ from the approved plan. After separate
build and database-creation approvals, the scoped command pattern is:

```bash
docker --context default compose --env-file "$E2E_ENV_FILE" -f compose.e2e.yaml -p "jobtracker-e2e-${E2E_RUN_ID}" config --quiet
docker --context default compose --env-file "$E2E_ENV_FILE" -f compose.e2e.yaml -p "jobtracker-e2e-${E2E_RUN_ID}" build backend frontend
docker --context default compose --env-file "$E2E_ENV_FILE" -f compose.e2e.yaml -p "jobtracker-e2e-${E2E_RUN_ID}" up -d --no-deps db
```

After verifying the new database container and volume by immutable ID, review
the guarded migration target and request approval for a one-off container that
performs only a read-only identity/schema check. It must report success only
when the guarded engine points to the exact new database, the server reports
the matching role, `public` schema and port 5432, and there are no existing
tables or views. This command creates a temporary Compose run container, so it
is a future live action requiring its own approval even though its SQL is
read-only:

```bash
docker --context default compose --env-file "$E2E_ENV_FILE" -f compose.e2e.yaml -p "jobtracker-e2e-${E2E_RUN_ID}" run --no-deps -e "MIGRATION_DATABASE_NAME=jobtracker_stack_${E2E_RUN_ID}" backend python -c '
import os
from sqlalchemy import inspect
from app.database import get_database_engine
from app.database_config import stack_database_target
from app.migration_safety import check_stack_target
target = stack_database_target(os.environ)
if os.environ.get("MIGRATION_DATABASE_NAME") != target.name:
    raise SystemExit("E2E migration target mismatch")
with get_database_engine().connect() as connection:
    check_stack_target(connection, target)
    schema = inspect(connection)
    if schema.get_table_names(schema="public") or schema.get_view_names(schema="public"):
        raise SystemExit("E2E database is not empty")
print("E2E database identity and empty schema verified")
'
```

Stop on any failure or unexpected output. Recheck the approved Docker identity
and request **separate migration approval** after this succeeds. The one-shot
upgrade is **not** part of API startup:

```bash
docker --context default compose --env-file "$E2E_ENV_FILE" -f compose.e2e.yaml -p "jobtracker-e2e-${E2E_RUN_ID}" run --no-deps -e "MIGRATION_DATABASE_NAME=jobtracker_stack_${E2E_RUN_ID}" backend python -m alembic upgrade 0005_user_profile
docker --context default compose --env-file "$E2E_ENV_FILE" -f compose.e2e.yaml -p "jobtracker-e2e-${E2E_RUN_ID}" run --no-deps -e "MIGRATION_DATABASE_NAME=jobtracker_stack_${E2E_RUN_ID}" backend python -m alembic current
```

The first command requires the separate read-only server/schema identity gate
described above. The second must report `0005_user_profile`; also verify the
expected tables through a read-only query against that same approved database.
Only then, with service-start approval, start `capture`, `backend`, and
`frontend` in that order, using the same explicit `--context`, `--env-file`,
`-f`, and `-p` arguments and `up -d --no-deps <service>`. For shutdown, use
`stop frontend backend capture db` with those same selectors after verifying
the exact project identity; no volume removal is implied.

The Mailpit image is pinned to the previously reviewed v1.32.0 image-index
digest in `compose.e2e.yaml`. Verify that digest and the target platform again
before a future pull. No existing Docker resource is evidence that a fresh E2E
run is safe to reuse.
