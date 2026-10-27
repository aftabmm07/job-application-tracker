# Isolated local container stack — operator runbook

This is an **offline plan**, not approval to build, start, connect to, or migrate
any container or database. Review every future command against the approved run
identity. Use only `compose.stack.yaml`; never combine it with `compose.yaml`,
`compose.demo-db.yaml`, or `compose.mail-capture.yaml`.

## Identity and limits

- Obtain approval for a **new** 16-character lowercase hexadecimal `STACK_RUN_ID`.
  Do not reuse the protected demo run ID or any existing stack identity.
- The Compose project is `jobtracker-stack-<run-id>`, database is
  `jobtracker_stack_<run-id>`, and role is `jobtracker_stack`. Compose scopes the
  `stack_pgdata` volume and both networks to that project. There are no external
  volumes, networks, or fixed container names.
- Only the frontend publishes a port: `127.0.0.1:18080:8080`. PostgreSQL and
  FastAPI have no host ports. PostgreSQL joins only the internal private network;
  the frontend joins only the edge network; the backend joins both.
- This is an HTTP **development** demo. `SESSION_COOKIE_SECURE=false` is valid
  only here. Do not expose port 18080 beyond loopback or treat this as a public
  production deployment. The stack database mode refuses `APP_ENV=production`.
- The email outbox remains disabled. `EMAIL_DELIVERY_MODE=none` prevents real
  outbound email, but new accounts cannot complete email verification through
  this stack. The existing local-capture sender is fixed to loopback *inside*
  the backend container and to Vite origins on port 5173. A Mailpit sidecar or
  shared network namespace needs a separate design and approval; do not attach
  the existing Mailpit container or change authentication settings to bypass it.

## Private settings, before any Docker operation

Use a fresh shell without tracing. The operator supplies the approved run ID;
the following code refuses malformed IDs and existing files. It creates a
private file outside the repository containing a fresh PostgreSQL password and
a separate stable CSRF secret. It never prints their values.

```bash
unset STACK_RUN_ID COMPOSE_PROJECT_NAME COMPOSE_FILE STACK_POSTGRES_PASSWORD STACK_CSRF_HMAC_SECRET
read -r -p 'Approved STACK_RUN_ID: ' STACK_RUN_ID
[[ "$STACK_RUN_ID" =~ ^[0-9a-f]{16}$ ]] || { echo 'Invalid run ID'; exit 1; }
export STACK_RUN_ID
STACK_ENV_FILE="$HOME/.config/jobtracker-stack/${STACK_RUN_ID}.env"
export STACK_ENV_FILE
umask 077
python3 - <<'PY'
import os
from pathlib import Path
import re
import secrets
import stat

run_id = os.environ['STACK_RUN_ID']
if not re.fullmatch(r'[0-9a-f]{16}', run_id):
    raise SystemExit('Invalid run ID')
directory = Path.home() / '.config' / 'jobtracker-stack'
directory.mkdir(mode=0o700, parents=True, exist_ok=True)
if (directory.is_symlink() or directory.stat().st_uid != os.getuid()
        or stat.S_IMODE(directory.stat().st_mode) != 0o700):
    raise SystemExit('Unsafe private settings directory')
path = directory / f'{run_id}.env'
fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
with os.fdopen(fd, 'w') as stream:
    stream.write(f'STACK_RUN_ID={run_id}\n')
    stream.write(f'STACK_POSTGRES_PASSWORD={secrets.token_urlsafe(48)}\n')
    stream.write(f'STACK_CSRF_HMAC_SECRET={secrets.token_urlsafe(48)}\n')
print('Private settings created; values withheld')
PY
```

Never place this file in the project, paste it into logs, or print unrestricted
`docker compose config` or container inspection output. Keep the private file
mode `0600` and its directory mode `0700`. Use a fresh shell for a later approved
run, set the same approved `STACK_RUN_ID` and `STACK_ENV_FILE`, and leave
`COMPOSE_PROJECT_NAME`, `COMPOSE_FILE`, `STACK_POSTGRES_PASSWORD`, and
`STACK_CSRF_HMAC_SECRET` unset. Shell values take precedence over `--env-file`.
Before *each* proposed Compose operation, privately verify the file's ownership,
permissions, exact three keys, nonempty secrets, and matching run ID. Stop on
any inherited override or mismatch; do not print the file or resolved Compose
configuration:

```bash
python3 - <<'PY'
import os
from pathlib import Path
import re
import stat

run_id = os.environ.get('STACK_RUN_ID', '')
path = Path(os.environ.get('STACK_ENV_FILE', ''))
expected = Path.home() / '.config' / 'jobtracker-stack' / f'{run_id}.env'
if (not re.fullmatch(r'[0-9a-f]{16}', run_id) or path != expected
        or path.parent.is_symlink() or not path.parent.is_dir()
        or path.parent.stat().st_uid != os.getuid()
        or stat.S_IMODE(path.parent.stat().st_mode) != 0o700
        or path.is_symlink() or not path.is_file()
        or path.stat().st_uid != os.getuid()
        or stat.S_IMODE(path.stat().st_mode) != 0o600
        or any(name in os.environ for name in (
            'COMPOSE_PROJECT_NAME', 'COMPOSE_FILE', 'STACK_POSTGRES_PASSWORD',
            'STACK_CSRF_HMAC_SECRET'))):
    raise SystemExit('Stack settings preflight failed')
lines = [line.partition('=') for line in path.read_text().splitlines()]
settings = {key: value for key, separator, value in lines if separator}
if (len(lines) != 3 or len(settings) != 3
        or set(settings) != {'STACK_RUN_ID', 'STACK_POSTGRES_PASSWORD', 'STACK_CSRF_HMAC_SECRET'}
        or settings['STACK_RUN_ID'] != run_id
        or len(settings['STACK_POSTGRES_PASSWORD']) < 16
        or len(settings['STACK_CSRF_HMAC_SECRET'].encode()) < 32):
    raise SystemExit('Stack settings preflight failed')
print('Private stack settings verified; values withheld')
PY
```

## Separately approved live sequence

These commands are **proposals only**. No command in this section has been run
as part of preparing the stack. Run each phase only after identity, collision,
image, and target reviews grant separate approval.
The Compose file declares the project name, and each command selects the same
name with `-p`. Neither mechanism proves that the project is new or that Docker
points to the approved daemon. Check context, endpoint, existing project
resources, and port collisions independently immediately before mutations.

1. Read-only preflight: verify the selected Docker context/endpoint, that the
   project and project-scoped volume/network names are unused, and that host
   port 18080 is free. Stop on any collision. Verify the private file's run ID
   matches the approved ID without showing secrets. Inspect only these narrow
   fields; do not print unrestricted Docker inspection output. The selected
   context must be the reviewed `default` context with the approved local Unix
   endpoint and daemon identity. An empty resource listing is required for a
   **new** run. A listener on either WSL or Windows port 18080 is a collision.

   ```bash
   docker context show
   docker context inspect default --format '{{.Endpoints.docker.Host}}'
   docker --context default info --format '{{.ID}}'
   docker --context default ps -a --filter "label=com.docker.compose.project=jobtracker-stack-${STACK_RUN_ID}" --format '{{.ID}} {{.Names}}'
   docker --context default volume ls --filter "name=jobtracker-stack-${STACK_RUN_ID}_stack_pgdata" --format '{{.Name}}'
   docker --context default network ls --filter "name=jobtracker-stack-${STACK_RUN_ID}_" --format '{{.Name}}'
   docker --context default ps --filter "publish=18080" --format '{{.ID}} {{.Ports}}'
   ss -ltn '( sport = :18080 )'
   powershell.exe -NoProfile -Command 'Get-NetTCPConnection -LocalPort 18080 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty LocalAddress'
   ```

   The expected new resources are project-scoped
   `jobtracker-stack-${STACK_RUN_ID}_stack_pgdata`,
   `jobtracker-stack-${STACK_RUN_ID}_edge`, and
   `jobtracker-stack-${STACK_RUN_ID}_private`. The name filters may also return
   partial matches: treat **any** output as a reason to stop and investigate,
   not as permission to adopt a resource. On a non-WSL host, use its equivalent
   passive host-listener check. Do not use a TCP probe or connect to PostgreSQL.
   Finally, run Compose syntax validation without displaying its resolved
   secret-bearing configuration:

   ```bash
   docker --context default compose --env-file "$STACK_ENV_FILE" -f compose.stack.yaml -p "jobtracker-stack-${STACK_RUN_ID}" config --quiet
   ```

2. With separate build approval, build **only** the new frontend/backend images.
   Image tags and the non-root Nginx behavior require live verification; no
   digest or runtime behavior has been verified offline.

   ```bash
   docker --context default compose --env-file "$STACK_ENV_FILE" -f compose.stack.yaml -p "jobtracker-stack-${STACK_RUN_ID}" build backend frontend
   ```

3. With separate database-creation approval, start only the new `db` service.
   PostgreSQL initializes only its new project-scoped database and volume.
   Verify immutable container/project identity, volume, network, no published
   database port, and health before allowing a connection.

   ```bash
   docker --context default compose --env-file "$STACK_ENV_FILE" -f compose.stack.yaml -p "jobtracker-stack-${STACK_RUN_ID}" up -d --no-deps db
   ```

4. With **separate migration approval**, verify the server-reported database,
   role, schema, empty-table state, and expected project identity first. A
   separately approved read-only connection check can use the same guarded
   backend target without displaying a URL or row data:

   ```bash
   docker --context default compose --env-file "$STACK_ENV_FILE" -f compose.stack.yaml -p "jobtracker-stack-${STACK_RUN_ID}" run --no-deps \
     backend python -c '
   import os
   from sqlalchemy import inspect, text
   from app.database import get_database_engine

   expected = ("jobtracker_stack_" + os.environ["STACK_RUN_ID"], "jobtracker_stack", "public", 5432)
   with get_database_engine().connect() as connection:
       identity = tuple(connection.execute(text("SELECT current_database(), current_user, current_schema(), inet_server_port()")).one())
       tables = inspect(connection).get_table_names(schema="public")
   if identity != expected or tables:
       raise SystemExit("Unexpected stack database identity or schema")
   print("Approved empty stack target")
   '
   ```

   Then
   run one guarded Alembic upgrade to the reviewed revision. The backend
   Dockerfile does not migrate at startup. This one-shot command leaves its
   stopped migration container for a later reviewed cleanup; it does not remove
   a volume. Stop on any identity or migration error; do not reset or retry.

   ```bash
   docker --context default compose --env-file "$STACK_ENV_FILE" -f compose.stack.yaml -p "jobtracker-stack-${STACK_RUN_ID}" run --no-deps \
     -e "MIGRATION_DATABASE_NAME=jobtracker_stack_${STACK_RUN_ID}" \
     backend python -m alembic upgrade 0005_user_profile
   ```

5. After the migration succeeds, verify its exact revision and metadata using
   a separately approved read-only Alembic operation on the same guarded
   target. Both commands require the matching `MIGRATION_DATABASE_NAME` and
   must finish successfully; `current` must report `0005_user_profile`, and
   `check` must report no pending model changes. Stop on mismatch. These checks
   do not substitute for the container, volume, and database identity checks.

   ```bash
   docker --context default compose --env-file "$STACK_ENV_FILE" -f compose.stack.yaml -p "jobtracker-stack-${STACK_RUN_ID}" run --no-deps \
     -e "MIGRATION_DATABASE_NAME=jobtracker_stack_${STACK_RUN_ID}" \
     backend python -m alembic current
   docker --context default compose --env-file "$STACK_ENV_FILE" -f compose.stack.yaml -p "jobtracker-stack-${STACK_RUN_ID}" run --no-deps \
     -e "MIGRATION_DATABASE_NAME=jobtracker_stack_${STACK_RUN_ID}" \
     backend python -m alembic check
   ```

6. Only after migration verification, start backend and frontend in that order:

   ```bash
   docker --context default compose --env-file "$STACK_ENV_FILE" -f compose.stack.yaml -p "jobtracker-stack-${STACK_RUN_ID}" up -d --no-deps backend
   docker --context default compose --env-file "$STACK_ENV_FILE" -f compose.stack.yaml -p "jobtracker-stack-${STACK_RUN_ID}" up -d --no-deps frontend
   ```

   Verify `http://localhost:18080/health` and
   `http://localhost:18080/health/db`, then the SPA routes and authenticated
   API behavior with a separately approved disposable test account. Health
   checks show process/connectivity health; they do **not** prove that Alembic
   reached the expected revision. Check revision/schema separately before
   browser mutations. Registration-to-verification remains blocked while email
   delivery is disabled.

## Shutdown and recovery

Stop only the approved project services, with the same Compose file and private
settings. `stop` preserves containers and the database volume. Do not use
`down -v`, `docker volume rm`, or commands aimed at the protected original or
demo projects.

```bash
docker --context default compose --env-file "$STACK_ENV_FILE" -f compose.stack.yaml -p "jobtracker-stack-${STACK_RUN_ID}" stop frontend backend db
```

On failure, record only sanitized exit codes, health states, and immutable
resource identities. Do not print environment variables, connection URLs,
verification links, cookies, or unrestricted logs. Check source rollback
against the private checkpoint separately from database recovery. Never treat a
source rollback as a database rollback. Any restart, migration repair, or volume
action needs a new identity check and approval.
