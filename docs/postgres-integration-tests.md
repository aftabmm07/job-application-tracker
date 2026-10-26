# Receipt-bound PostgreSQL 17 tests — NOT APPROVED TO PROVISION

Task #61.3F changes harness/tests/documentation only. Obtain a **second read-only
safety review**, then separate explicit approval for every infrastructure and
PostgreSQL operation below. Nothing here authorizes execution. Never use project
Compose, `job-tracker-db`, `postgres_data`, backups, restore/rehearsal databases,
or `scripts.rehearse_postgres` for these tests.

## What the workflow enforces

Creation generates a fresh 128-bit run ID, separate 256-bit password, and random
receipt nonce internally. It refuses inherited run/credential/receipt variables;
there is no option to adopt a container. The receipt is created exclusively at
`/tmp/jobtracker-pg613-<run-id>/receipt.json`, outside the repository, in an
owner-only 0700 directory with a 0600 regular file. Symlinks, hardlinks, wrong
ownership/permissions, modified approved configuration, and receipts older than
two hours are rejected. Do not print, copy, commit or upload receipts: they hold
the disposable password. Restrictive permissions are not encryption.

The generated PostgreSQL role is a superuser only inside this new disposable
cluster, allowing its test databases to be created. It is never a project or
production credential.

The receipt binds the run and approved settings to the Docker context, local
Unix-socket endpoint, daemon ID, image ID, network ID and immutable container ID.
Remote SSH/TCP contexts and Docker endpoint environment overrides are rejected.
Immediately before each network/container mutation, the context/endpoint tuple is
rechecked, the daemon ID is checked through the saved Unix endpoint, and the
mutation uses `--host <saved-unix-endpoint>` instead of resolving a mutable context.
A privileged process replacing that socket/daemon between checks remains outside
the trusted-host boundary; these checks cannot make separate Docker RPCs atomic.
This supports local Linux/WSL Docker through a local Unix socket; other platforms
need a separately reviewed adaptation. A hostile same-user process, Docker
administrator, or Unix-socket proxy is outside this protection boundary.

Creation checks exact container/network names and reserves `127.0.0.1:55433`
before any network/container creation. Failed Docker queries never mean "absent".
Docker performs the final atomic name/binding checks; port races fail closed.
The immutable container ID is recorded only after `docker create` succeeds and
is persisted/verified **before `docker start`**, which initializes PostgreSQL.
Only the newly created container is started. A bounded Docker health check waits
for its loopback PostgreSQL readiness; no external PostgreSQL connection occurs
in the creation helper. Creation approval therefore includes initialization and
readiness probes inside this new disposable container.

There is no automatic stop/remove/replacement after any failure. A failed create
may leave a network or stopped container, and its receipt is marked failed.
Creation failures print only the private receipt's path to stderr (visible even
when stdout is captured); they never print its contents or credentials. Use that
locator to request recovery review, not to reset state or authorize cleanup.
A hard kill before this diagnostic can still leave an undisplayed receipt in the
owner-only run directory; discovering those resources needs separate review.
Partial provisioning likewise becomes failed, not an invitation to retry/adopt.
Request a separately reviewed recovery procedure for those exact resources.

Operations acquire an exclusive receipt lock and a fresh nonce bound to the
operator PID, Linux boot ID and process start time. A nonce only authorizes the
current operation while that process lives. State transitions are single-use:

`created → provisioned → validated → tested → consumed`

Intermediate leases are `provisioning`, `validating`, `testing`, and `cleaning`.
Repeated operations, overlapping operations, interrupted leases, failed/expired
receipts and consumed receipts are refused. Cleanup is also allowed from a valid
created/provisioned/validated state, with separate approval. Any completed pytest
process produces state `tested`, and its exit code is propagated unchanged:
0 is success, 1 is failed tests, 2 is interruption, 3 is an internal error,
4 is a usage error, and 5 means no tests collected. `tested` records an attempted
run, not a passing verdict. A subprocess exception/timeout instead produces
`failed`, requiring recovery review. Never edit a receipt to reset state.

## Network and storage arrangement

Each run creates its **own internal user-defined bridge**, with inter-container
communication disabled (`com.docker.network.bridge.enable_icc=false`) and default
host binding `127.0.0.1`. The container is attached only to that network; actual
network inspection must show exactly that one member. No existing network is
attached. PostgreSQL is published only at **127.0.0.1:55433**. Default networking,
extra members, changed network options or a different binding cause refusal.

Docker's internal network blocks ordinary routed external connectivity; separate
bridges isolate unrelated containers. Host access is intentionally retained for
these tests. This is **not** complete host isolation or protection from privileged
Docker users, modified host firewall rules, or every Docker Desktop networking
variation. See [Docker bridge documentation](https://docs.docker.com/engine/network/drivers/bridge/)
and [internal networks](https://docs.docker.com/reference/cli/docker/network/create/#internal).
Do not weaken isolation if localhost publishing fails on a particular engine:
stop and review that platform. Real network behavior remains untested here.

The official [PostgreSQL 17 Bookworm Dockerfile](https://github.com/docker-library/postgres/blob/master/17/bookworm/Dockerfile)
uses `/var/lib/postgresql/data` for both PGDATA and VOLUME. The workflow verifies
those image settings and uses the inspected immutable image ID. Its only storage
mount is tmpfs at that path (`rw,nosuid,size=1g`), overriding the image volume.
Actual image, timestamps, mounts, labels, privilege settings, startup command,
network and both configured/actual port bindings are verified before PostgreSQL
access. Bind mounts, persistent volumes and shared containers are refused.
No Docker socket or host database paths are mounted.

## Database and reporting guards

Only `postgresql+psycopg`, literal `127.0.0.1:55433`, the generated role, and the
six exact databases `<role>_{api,fresh,legacy,bigint,char,timestamp}` are allowed.
`jobtracker_db`, port 5432, URL query overrides, DSNs, libpq services and unrelated
`PG*` environment overrides are rejected before DBAPI connection. An environment
run/password alone cannot authorize execution without a current receipt lease.
Every new physical connection verifies live Docker provenance before connecting,
then PostgreSQL 17, the server startup marker, database, role, public schema and
PGDATA before caller SQL. Migrations use these supplied connections, not .env
migration URLs. Do not start Uvicorn: its unused runtime engine uses port 5432;
API tests override the shared dependency and block accidental fallback calls.

The test command removes all inherited `PYTEST_*` settings before importing
pytest, then sets a fixed empty `PYTEST_ADDOPTS` and disables distribution plugin
autoload. This removes `PYTEST_PLUGINS`, `PYTEST_DEBUG` and other inherited pytest
injection/debug settings. Ini `addopts` is ignored by the runner, and the approved
reporting plugin is explicitly loaded early with `-p`. The required safe-reporting plugin enforces FD capture, short tracebacks,
no locals/full tracebacks/live logging, and refuses debuggers/debug logs.
Debug flags are also rejected before pytest can open a debug file.
Built-in pytest file logging is forced to the null device for both CLI and ini
settings before the logging plugin opens a file. Requested log files are neither
created nor truncated; unredacted records never reach those files. It redacts
registered/environment secrets, PostgreSQL URLs, password/token fields, bearer
and CSRF-sized tokens, full cookie lines, captured output and JUnit failure reports.
The parent captures and redacts child stdout/stderr again before displaying it.
Cookie assertions check booleans instead of printing complete header values.
Offline tests intentionally fail using synthetic secrets to exercise this path,
including inherited-plugin injection and CLI/ini file logging. This does not
sanction custom file handlers or third-party exporters.

Do not bypass the runner, enable shell tracing/SQL echo, add reporting plugins,
dump Docker inspection output, print receipts, use debugger/profiler artifacts,
or upload arbitrary raw logs. Redaction is defence in depth, not a guarantee for
arbitrary binary encodings, malicious plugins or custom external exporters.

## Known blocker — Docker Desktop isolated-network connectivity (October 2026)

**Status: Unresolved. Live PostgreSQL integration tests have not been completed.**

An explicitly authorized attempt to create a disposable PostgreSQL 17 test environment encountered a networking incompatibility with the current Docker Desktop/WSL setup.

The disposable container was running and healthy, and Docker recorded the requested host binding `127.0.0.1:55433:5432`. However, inspection showed that the port was not actually published (`NetworkSettings.Ports` reported `{"5432/tcp": []}`). A direct TCP connection from WSL to the container's internal-network address also timed out.

The harness correctly refuses this configuration because its provenance validation requires the configured and actual port bindings to match.

**Verified offline results:** All 62 tests in `tests/test_postgres_provenance.py` passed, including regression checks for missing port publication and the internal-network creation flag.

**Not verified:** Live PostgreSQL integration tests, database migrations against the disposable cluster, and the complete database-backed authentication workflow.

**Resource preservation:** The failed disposable test container, its internal network, and its failed receipt remain preserved for a separately authorized recovery review. They must not be reused, modified, or cleaned up without explicit approval.

**Decision:** Defer networking changes while continuing application development. Do not remove `--internal`, weaken provenance checks, substitute the original project database, or bypass receipt authorization to make the tests run.

Possible alternative architectures were reviewed, including a shared-network-namespace test runner. None has been approved for implementation. A future solution requires a separate security review and explicit authorization before any infrastructure changes.

## Offline validation — no infrastructure approval needed

Use the locked Python 3.14.4 Linux/WSL environment from setup.md. From `backend/`:

```bash
RUN_POSTGRES_OWNERSHIP_TESTS=0 RUN_ISOLATED_POSTGRES_TESTS=0 \
  python -m pytest -q --ignore=tests/postgresql --ignore=tests/test_postgres_ownership.py
git diff --check
```

These tests block real Psycopg connections. Docker and port reservation in the
safety tests are mocked; they never operate infrastructure. The integration suite
retains duplicate registration, login rotation, logout/renewal, lock-wait expiry,
timeout rollback, concurrent cleanup, ownership and migration coverage. It is
not enabled in CI and has not run against PostgreSQL in this task.

## Exact proposed commands — DO NOT EXECUTE before review and approval

Use a fresh dedicated Bash shell and the locked virtualenv, from `backend/`.
No `PG_TEST_*`, libpq `PG*`, `DOCKER_HOST`, `DOCKER_CONTEXT`, TLS override or tracing
settings should be inherited. Do not unset a remote endpoint merely to bypass a
refusal: review/select the intended local Docker context first.

If the image is not already present, this is a **separately approved** image
acquisition example, only after confirming `default` is the intended local
Unix-socket context. Substitute another context only after review:

```bash
docker --context default pull postgres:17-bookworm
```

Do not use a raw `docker run`/`create` command. The workflow below performs the
preflights, creates the private receipt, network and container, verifies them,
and polls container health with a 45-second budget (each Docker call also has
a 30-second timeout). Every operation is checked;
there is no fallback or automatic resource removal.

```bash
set -euo pipefail
export RUN_ISOLATED_POSTGRES_TESTS=1
pg_test_receipt="$(python -m scripts.postgres_test_environment create --approve-isolated-operation)"
export PG_TEST_RECEIPT="$pg_test_receipt"
printf 'Receipt for review: %s\n' "$PG_TEST_RECEIPT"
```

The assignment is deliberately separate from `export`: creation failure must
propagate to the shell. Creation's stdout contains only the receipt path, not credentials, and command
substitution captures it. The explicit `printf` then displays that non-secret
locator so it can be retained if the shell later exits. Stop after creation and obtain approval for subsequent steps.
Each operation below separately requires approval; execute in this order:

```bash
python -m scripts.postgres_test_environment provision --receipt "$PG_TEST_RECEIPT" --approve-isolated-operation
python -m scripts.postgres_test_environment validate --receipt "$PG_TEST_RECEIPT" --approve-isolated-operation
python -m scripts.postgres_test_environment test --receipt "$PG_TEST_RECEIPT" --approve-isolated-operation
```

Provisioning requires exactly the initial empty API database and PostgreSQL's
three default databases; it creates only the five run-specific databases above.
Validation checks all six identities. The runner executes the existing PostgreSQL
suite sequentially (no xdist), using independent sessions and real commits, at
most two hashing workers, unchanged Argon2id, bounded synchronization/database
timeouts, and a 600-second subprocess deadline. Live Docker checks add overhead;
lock observers are opened before holding row locks. Synthetic API rows are removed
only inside the approved disposable API database; migration databases are single-use.

If a command fails, **stop**. Do not continue with later lines, rerun provisioning,
reset a receipt, change the network, or adopt an existing container. Any nonzero pytest
exit under `set -e` also exits this dedicated shell; retain the displayed receipt
path securely for a separately approved cleanup from another dedicated shell.

## Exact proposed cleanup — separate approval required

After saving only redacted test results, use the same receipt. If opening another
shell, re-export `RUN_ISOLATED_POSTGRES_TESTS=1` and the **exact displayed receipt
path**, never reconstruct credentials or generate a replacement receipt.

```bash
python -m scripts.postgres_test_environment cleanup --receipt "$PG_TEST_RECEIPT" --approve-isolated-operation
unset PG_TEST_RECEIPT RUN_ISOLATED_POSTGRES_TESTS
```

Cleanup verifies the receipt/endpoint, immutable ID, live configuration, network
membership and PostgreSQL marker before stopping/removing that exact ID. It
rechecks the network is empty before removing only its recorded immutable ID.
It uses no `-v`, volume prune, Compose teardown or database-drop command. The
consumed receipt stays as a private tombstone with its password removed; it
cannot be reused. Stale receipts, stopped/unreachable containers, failed leases
or changed resources require a separately reviewed recovery procedure. There is
intentionally no recovery/adoption bypass in this implementation.

### Email verification migration completeness

The current head is `0003_email_verification_tokens`, following `0002_auth_models`.
Fresh disposable databases now include `email_verification_tokens`; the API fixture
requires that table and checks it is empty before each test. Fixture cleanup deletes
synthetic verification rows before users. Fresh and legacy migration expectations
include the new head/table. No creation receipts, connection guards or approval
requirements change. Offline SQLite checks do not validate PostgreSQL behavior;
these updated PostgreSQL tests have not been executed as part of this change.
Do not reuse an existing disposable environment or treat example names as approval.
