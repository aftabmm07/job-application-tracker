"""Fail-fast, receipt-bound operations. Implementation only until separately approved."""
import argparse
import json
import os
import re
import secrets
import subprocess
import sys
import time

from sqlalchemy import inspect, text

from scripts.postgres_test_harness import CASES, TestTarget, UnsafeTestTarget
from scripts.postgres_test_provenance import (
    DATA, IMAGE, OPTIONS, approved_configuration, available_port, docker, docker_mutation, endpoint,
    locked_receipt, new_receipt, process_identity, require_operation, refuse,
    validate_network, verify_resources, write_receipt,
)


def identifier(value):
    if not re.fullmatch(r'[0-9a-f]{64}', value):
        refuse()
    return value


def create():
    if os.environ.get('RUN_ISOLATED_POSTGRES_TESTS') != '1':
        refuse()
    # A new approval/run is generated internally. Never accept old run variables.
    if any(key.startswith('PG') for key in os.environ):
        refuse()
    target = TestTarget(secrets.token_hex(16), secrets.token_hex(32))
    path, stream = new_receipt(target)
    receipt = dict(version=1, run_id=target.run_id, password=target.password,
                   issued_at=time.time(), receipt_nonce=secrets.token_hex(16), state='creating',
                   approved=approved_configuration(target))
    with stream:
        write_receipt(stream, receipt)
        try:
            receipt['docker'] = endpoint()
            context = receipt['docker']['context']
            # Exact names; inspect/list failure never means "absent".
            names = docker(['container', 'ls', '--all', '--format', '{{.Names}}'], context).splitlines()
            networks = docker(['network', 'ls', '--format', '{{.Name}}'], context).splitlines()
            if target.container in names or receipt['approved']['network_name'] in networks:
                refuse()
            image = json.loads(docker(['image', 'inspect', IMAGE], context))[0]
            if (image['Config'].get('Volumes') != {DATA: {}}
                    or f'PGDATA={DATA}' not in image['Config'].get('Env', [])):
                refuse()
            receipt['image_id'] = 'sha256:' + identifier(image['Id'].removeprefix('sha256:'))
            receipt['image_digests'] = image.get('RepoDigests') or []
            labels = ['--label', f'jobtracker.test-run={target.run_id}',
                      '--label', f'jobtracker.receipt={receipt["receipt_nonce"]}',
                      '--label', 'jobtracker.disposable=true']
            with available_port():
                command = ['network', 'create', '--driver', 'bridge', '--internal'] + labels
                for name, value in OPTIONS.items():
                    command += ['--opt', f'{name}={value}']
                receipt['network_id'] = identifier(docker_mutation(command + [receipt['approved']['network_name']], receipt))
                write_receipt(stream, receipt)  # Preserve evidence if later creation fails.
                network = json.loads(docker(['network', 'inspect', receipt['network_id']], context))[0]
                validate_network(receipt, network, [])
            environment = dict(os.environ, POSTGRES_PASSWORD=target.password)
            command = ['create', '--name', target.container, '--network', receipt['network_id']]
            command += labels + ['--publish', '127.0.0.1:55433:5432', '--tmpfs', f'{DATA}:rw,nosuid,size=1g',
                                 '--env', f'PGDATA={DATA}', '--env', f'POSTGRES_USER={target.role}',
                                 '--env', f'POSTGRES_DB={target.database("api")}', '--env', 'POSTGRES_PASSWORD',
                                 '--health-cmd', f'pg_isready -h 127.0.0.1 -U {target.role} -d {target.database("api")}',
                                 '--health-interval=1s', '--health-timeout=2s', '--health-retries=30',
                                 receipt['image_id'], 'postgres', '-c', f'jobtracker.test_run={target.run_id}']
            # No --rm and no automatic recovery. ID is recorded ONLY on success.
            receipt['container_id'] = identifier(docker_mutation(command, receipt, environment))
            information = json.loads(docker(['inspect', '--type=container', receipt['container_id']], context))[0]
            receipt['container_created'] = information['Created']
            # Persist the immutable creation ID BEFORE starting entrypoint/initdb.
            write_receipt(stream, receipt)
            verify_resources(target, receipt, running=False)
            docker_mutation(['start', receipt['container_id']], receipt)
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                status = docker(['inspect', '--format', '{{.State.Health.Status}}', receipt['container_id']], context)
                if status == 'healthy':
                    break
                if status != 'starting':
                    refuse()
                time.sleep(0.2)
            else:
                refuse()
            verify_resources(target, receipt)
            receipt['state'] = 'created'
            write_receipt(stream, receipt)
        except Exception:
            receipt['state'] = 'failed'
            # The path contains only a random run ID, never credentials. stderr
            # remains visible when successful stdout is captured by the shell.
            print(f'Creation failed; private recovery receipt: {path}', file=sys.stderr)
            write_receipt(stream, receipt)
            raise UnsafeTestTarget('Creation failed; resources retained for separate recovery review') from None
    return path


def provision(target):
    require_operation(target, 'provisioning')
    target.authorize()
    engine = target.engine('api')
    try:
        with engine.connect().execution_options(isolation_level='AUTOCOMMIT') as connection:
            names = set(connection.scalars(text('SELECT datname FROM pg_database')))
            if names != {'postgres', 'template0', 'template1', target.database('api')}:
                refuse()
            inspector = inspect(connection)
            if inspector.get_table_names() or inspector.get_view_names() or inspector.get_materialized_view_names():
                refuse()
            for case in CASES[1:]:
                connection.exec_driver_sql(f'CREATE DATABASE "{target.database(case)}" OWNER "{target.role}"')
    finally:
        engine.dispose()


def validate(target):
    require_operation(target, 'validating')
    for case in CASES:
        engine = target.engine(case)
        try:
            with engine.connect():
                pass
        finally:
            engine.dispose()


def cleanup(target):
    require_operation(target, 'cleaning')
    receipt = verify_resources(target)
    engine = target.engine('api')
    try:
        with engine.connect() as connection:
            connection.execute(text('SELECT 1'))  # Marker checked before any stop.
    finally:
        engine.dispose()
    # Recheck endpoint, immutable identity, network membership and mounts.
    verify_resources(target)
    context = receipt['docker']['context']
    docker_mutation(['stop', '--time=10', receipt['container_id']], receipt)
    docker_mutation(['rm', receipt['container_id']], receipt)
    network = json.loads(docker(['network', 'inspect', receipt['network_id']], context))[0]
    validate_network(receipt, network, [])
    docker_mutation(['network', 'rm', receipt['network_id']], receipt)


def run_tests(target):
    require_operation(target, 'testing')
    from scripts.postgres_test_reporting import safe_pytest_environment
    environment = dict(safe_pytest_environment(os.environ), APP_ENV='development', APP_DATABASE_NAME=target.database('api'),
                       POSTGRES_PASSWORD='unused-offline-runtime-password', PASSWORD_HASH_MAX_CONCURRENCY='2',
                       PYTEST_DISABLE_PLUGIN_AUTOLOAD='1', PYTEST_ADDOPTS='')
    # Captured output is redacted again before display, including subprocess failures.
    command = [sys.executable, '-m', 'pytest', '-p', 'scripts.postgres_test_reporting',
               '-o', 'addopts=', '-q', '--isolated-safe-reports', '--tb=short', 'tests/postgresql']
    result = subprocess.run(command, env=environment, cwd=os.path.dirname(os.path.dirname(__file__)),
                            capture_output=True, text=True, timeout=600)
    from scripts.postgres_test_reporting import redact
    print(redact(result.stdout))
    print(redact(result.stderr), file=sys.stderr)
    return result.returncode


TRANSITIONS = {'provision': ({'created'}, 'provisioning', 'provisioned'),
               'validate': ({'provisioned'}, 'validating', 'validated'),
               'test': ({'validated'}, 'testing', 'tested'),
               'cleanup': ({'created', 'provisioned', 'validated', 'tested'}, 'cleaning', 'consumed')}


def operate(action, path):
    if os.environ.get('RUN_ISOLATED_POSTGRES_TESTS') != '1':
        refuse()
    initial, active, final = TRANSITIONS[action]
    with locked_receipt(path) as (stream, receipt):
        if receipt['state'] not in initial or receipt.get('operation_nonce'):
            refuse()  # No repeats, interrupted leases, failed or consumed receipts.
        target = TestTarget(receipt['run_id'], receipt['password'])
        receipt.update(state=active, operation_nonce=secrets.token_hex(16), operator=process_identity())
        write_receipt(stream, receipt)
        previous = {key: os.environ.get(key) for key in ('PG_TEST_RECEIPT', 'PG_TEST_OPERATION_NONCE')}
        os.environ.update(PG_TEST_RECEIPT=str(path), PG_TEST_OPERATION_NONCE=receipt['operation_nonce'])
        try:
            verify_resources(target)
            result = {'provision': provision, 'validate': validate, 'test': run_tests, 'cleanup': cleanup}[action](target)
            receipt.update(state=final, operation_nonce=None, operator=None)
            if action == 'cleanup':
                receipt['password'] = ''  # Retain a consumed tombstone, not reusable credentials.
            write_receipt(stream, receipt)
            return result or 0
        except Exception:
            receipt.update(state='failed', operation_nonce=None, operator=None)
            write_receipt(stream, receipt)
            raise UnsafeTestTarget('Operation failed; separate recovery review required') from None
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['create', *TRANSITIONS])
    parser.add_argument('--receipt')
    parser.add_argument('--approve-isolated-operation', action='store_true', required=True)
    args = parser.parse_args()
    try:
        if args.action == 'create':
            if args.receipt:
                refuse()
            print(create())
        else:
            if os.environ.get('RUN_ISOLATED_POSTGRES_TESTS') != '1' or not args.receipt:
                refuse()
            status = operate(args.action, args.receipt)
            if status:
                parser.exit(status, 'Isolated tests failed; review the redacted report.\n')
            print('Isolated operation completed.')
    except Exception:
        parser.exit(1, 'Isolated operation refused or failed; no automatic cleanup. Review recovery separately.\n')


if __name__ == '__main__':
    main()
