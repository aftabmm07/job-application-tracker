"""Offline creation/receipt tests. Docker, port binds and PostgreSQL are mocked."""
from contextlib import contextmanager
from copy import deepcopy
import json
from datetime import UTC, datetime
import os
import stat
import time
from unittest.mock import MagicMock

import pytest

from scripts import postgres_test_environment as environment
from scripts import postgres_test_provenance as provenance
from scripts.postgres_test_harness import TestTarget, UnsafeTestTarget

RUN, SECRET = '1a' * 16, '2b' * 32
CONTAINER, NETWORK, IMAGE = 'a' * 64, 'b' * 64, 'sha256:' + 'c' * 64
ENDPOINT = {'context': 'default', 'endpoint': 'unix:///var/run/docker.sock', 'daemon_id': 'synthetic-daemon'}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(provenance, 'ROOT', tmp_path)
    monkeypatch.setenv('RUN_ISOLATED_POSTGRES_TESTS', '1')
    for name in ('PG_TEST_RECEIPT', 'PG_TEST_RUN_ID', 'PG_TEST_PASSWORD', 'PG_TEST_OPERATION_NONCE'):
        monkeypatch.delenv(name, raising=False)
    target = TestTarget(RUN, SECRET)
    receipt = dict(version=1, run_id=RUN, password=SECRET, issued_at=time.time(), receipt_nonce='3d' * 16,
                   approved=provenance.approved_configuration(target), state='created', docker=ENDPOINT,
                   container_id=CONTAINER, network_id=NETWORK, image_id=IMAGE,
                   container_created=datetime.now(UTC).isoformat())
    path, stream = provenance.new_receipt(target)
    with stream:
        provenance.write_receipt(stream, receipt)
    return target, receipt, path


def container_info(target, receipt):
    ports = {'5432/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '55433'}]}
    return dict(Id=CONTAINER, Image=IMAGE, Name='/' + target.container, Created=receipt['container_created'],
        Config=dict(Labels={'jobtracker.test-run': RUN, 'jobtracker.receipt': receipt['receipt_nonce'],
                            'jobtracker.disposable': 'true'},
            Env=[f'PGDATA={provenance.DATA}', f'POSTGRES_USER={target.role}',
                 f'POSTGRES_DB={target.database("api")}', f'POSTGRES_PASSWORD={SECRET}'],
            Cmd=['postgres', '-c', f'jobtracker.test_run={RUN}']),
        HostConfig=dict(PortBindings=ports, Tmpfs={provenance.DATA: 'rw,nosuid,size=1g'}, NetworkMode=NETWORK),
        Mounts=[{'Type': 'tmpfs', 'Destination': provenance.DATA}], State={'Running': True},
        NetworkSettings=dict(Ports=ports, Networks={receipt['approved']['network_name']: {'NetworkID': NETWORK}}))


def network_info(receipt, members=None):
    return dict(Id=NETWORK, Name=receipt['approved']['network_name'], Driver='bridge', Scope='local',
                Internal=True, EnableIPv6=False, Attachable=False, Ingress=False, Options=provenance.OPTIONS,
                Labels={'jobtracker.test-run': RUN, 'jobtracker.receipt': receipt['receipt_nonce']},
                Containers={key: {} for key in (members if members is not None else [CONTAINER])})


def test_private_receipt_permissions_and_no_reuse(setup):
    target, receipt, path = setup
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert provenance.read_receipt(path) == receipt
    with pytest.raises(FileExistsError):
        provenance.new_receipt(target)


@pytest.mark.parametrize('kind', ['missing', 'stale', 'future', 'permissions', 'parent_permissions', 'symlink', 'configuration', 'run_id'])
def test_invalid_receipt_fails_before_docker(setup, monkeypatch, kind):
    target, receipt, path = setup
    docker = MagicMock(side_effect=AssertionError('Docker must not run'))
    monkeypatch.setattr(provenance, 'docker', docker)
    if kind == 'missing':
        path.unlink()
    elif kind == 'permissions':
        path.chmod(0o644)
    elif kind == 'parent_permissions':
        path.parent.chmod(0o755)
    elif kind == 'symlink':
        original = path.with_name('original.json')
        path.rename(original)
        path.symlink_to(original)
    else:
        if kind == 'stale':
            receipt['issued_at'] -= provenance.TTL_SECONDS + 1
        elif kind == 'future':
            receipt['issued_at'] += 60
        elif kind == 'configuration':
            receipt['approved']['port'] = 5432
        else:
            receipt['run_id'] = '4e' * 16
        path.write_text(json.dumps(receipt))
    with pytest.raises((UnsafeTestTarget, FileNotFoundError)):
        provenance.read_receipt(path)
    docker.assert_not_called()


@pytest.mark.parametrize('state', ['provisioned', 'consumed', 'failed', 'creating', 'provisioning'])
def test_reused_or_interrupted_receipt_cannot_provision(setup, monkeypatch, state):
    target, receipt, path = setup
    receipt['state'] = state
    path.write_text(json.dumps(receipt))
    verify = MagicMock(side_effect=AssertionError('No external operation allowed'))
    monkeypatch.setattr(environment, 'verify_resources', verify)
    with pytest.raises(UnsafeTestTarget):
        environment.operate('provision', path)
    verify.assert_not_called()


def test_concurrent_operation_receipt_lock(setup):
    target, receipt, path = setup
    with provenance.locked_receipt(path):
        with pytest.raises(UnsafeTestTarget):
            with provenance.locked_receipt(path):
                pytest.fail('Must not acquire an already held operation lease')


@pytest.mark.parametrize('kind', ['endpoint', 'context', 'daemon', 'container_id', 'image', 'network', 'mount', 'port', 'name', 'extra_member', 'internal', 'default_network', 'bind', 'unpublished_port'])
def test_live_identity_or_resources_mismatch_refused(setup, monkeypatch, kind):
    target, receipt, path = setup
    actual_endpoint = dict(ENDPOINT)
    container = container_info(target, receipt)
    network = deepcopy(network_info(receipt))
    if kind in {'endpoint', 'context'}:
        actual_endpoint[kind] = 'unexpected'
    elif kind == 'daemon':
        actual_endpoint['daemon_id'] = 'other'
    elif kind == 'container_id':
        container['Id'] = 'd' * 64
    elif kind == 'image':
        container['Image'] = 'sha256:' + 'd' * 64
    elif kind == 'network':
        container['NetworkSettings']['Networks']['project-default'] = {'NetworkID': 'other'}
    elif kind == 'mount':
        container['Mounts'].append({'Type': 'volume', 'Destination': '/existing'})
    elif kind == 'port':
        container['HostConfig']['PortBindings'] = {'5432/tcp': [{'HostIp': '0.0.0.0', 'HostPort': '5432'}]}
    elif kind == 'unpublished_port':
        container['NetworkSettings']['Ports'] = {'5432/tcp': []}
    elif kind == 'name':
        container['Name'] = '/job-tracker-db'
    elif kind == 'extra_member':
        network['Containers']['d' * 64] = {}
    elif kind == 'internal':
        network['Internal'] = False
    elif kind == 'default_network':
        container['HostConfig']['NetworkMode'] = 'bridge'
    else:
        container['HostConfig']['Binds'] = ['postgres_data:/var/lib/postgresql/data']
    monkeypatch.setattr(provenance, 'endpoint', lambda: actual_endpoint)
    calls = []
    def docker(args, *unused):
        calls.append(args)
        assert args[0] in {'inspect', 'network'}
        return json.dumps([network if args[0] == 'network' else container])
    monkeypatch.setattr(provenance, 'docker', docker)
    with pytest.raises(UnsafeTestTarget):
        provenance.verify_resources(target, receipt)
    assert not any('stop' in call or 'rm' in call for call in calls)


def test_matching_resources_verify_without_mutation(setup, monkeypatch):
    target, receipt, path = setup
    monkeypatch.setattr(provenance, 'endpoint', lambda: ENDPOINT)
    docker = MagicMock(side_effect=[json.dumps([container_info(target, receipt)]), json.dumps([network_info(receipt)])])
    monkeypatch.setattr(provenance, 'docker', docker)
    assert provenance.verify_resources(target, receipt) == receipt
    assert [call.args[0][0] for call in docker.call_args_list] == ['inspect', 'network']


def test_failed_provision_is_terminal_and_does_not_cleanup(setup, monkeypatch):
    target, receipt, path = setup
    monkeypatch.setattr(environment, 'verify_resources', lambda *args: receipt)
    monkeypatch.setattr(environment, 'provision', MagicMock(side_effect=RuntimeError('synthetic failure')))
    cleanup = MagicMock(side_effect=AssertionError('No automatic cleanup'))
    monkeypatch.setattr(environment, 'cleanup', cleanup)
    with pytest.raises(UnsafeTestTarget):
        environment.operate('provision', path)
    assert provenance.read_receipt(path)['state'] == 'failed'
    with pytest.raises(UnsafeTestTarget):
        environment.operate('cleanup', path)
    cleanup.assert_not_called()


def test_receipt_lease_requires_current_process_and_nonce(setup, monkeypatch):
    target, receipt, path = setup
    monkeypatch.setenv('PG_TEST_RECEIPT', str(path))
    with pytest.raises(UnsafeTestTarget):
        provenance.authorize_receipt(target)
    receipt.update(state='testing', operation_nonce='lease-nonce', operator=provenance.process_identity())
    path.write_text(json.dumps(receipt))
    monkeypatch.setenv('PG_TEST_OPERATION_NONCE', 'wrong-nonce')
    with pytest.raises(UnsafeTestTarget):
        provenance.authorize_receipt(target)
    monkeypatch.setenv('PG_TEST_OPERATION_NONCE', 'lease-nonce')
    assert provenance.authorize_receipt(target) == receipt
    receipt['operator']['start_ticks'] = '0'
    path.write_text(json.dumps(receipt))
    with pytest.raises(UnsafeTestTarget):
        provenance.authorize_receipt(target)


@pytest.mark.parametrize('failure', ['name', 'network_name', 'port', 'docker', 'image', 'run'])
def test_creation_collision_or_failure_never_adopts_or_cleans(tmp_path, monkeypatch, failure):
    monkeypatch.setattr(provenance, 'ROOT', tmp_path)
    monkeypatch.setenv('RUN_ISOLATED_POSTGRES_TESTS', '1')
    for name in ('PG_TEST_RECEIPT', 'PG_TEST_RUN_ID', 'PG_TEST_PASSWORD', 'PG_TEST_OPERATION_NONCE'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(environment.secrets, 'token_hex', lambda count: RUN if count == 16 else SECRET)
    monkeypatch.setattr(environment, 'endpoint', lambda: ENDPOINT)
    target = TestTarget(RUN, SECRET)
    seen = []

    @contextmanager
    def port():
        if failure == 'port':
            raise UnsafeTestTarget('occupied')
        yield

    def docker(args, *unused):
        seen.append(args)
        if failure == 'docker':
            raise UnsafeTestTarget('Docker unavailable')
        if args[:2] == ['container', 'ls']:
            return target.container if failure == 'name' else ''
        if args[:2] == ['network', 'ls']:
            return target.container + '-net' if failure == 'network_name' else ''
        if args[:2] == ['image', 'inspect']:
            volumes = {'/unexpected': {}} if failure == 'image' else {provenance.DATA: {}}
            return json.dumps([{'Id': IMAGE, 'Config': {'Volumes': volumes, 'Env': [f'PGDATA={provenance.DATA}']}}])
        if args[:2] == ['network', 'create']:
            return NETWORK
        if args[:2] == ['network', 'inspect']:
            receipt = json.loads(provenance.receipt_path(RUN).read_text())
            return json.dumps([network_info(receipt, [])])
        if args[0] == 'create':
            raise UnsafeTestTarget('Docker refused startup; possible port race')
        raise AssertionError('Unexpected Docker operation')

    monkeypatch.setattr(environment, 'available_port', port)
    monkeypatch.setattr(environment, 'docker', docker)
    monkeypatch.setattr(environment, 'docker_mutation', lambda args, receipt, transport_environment=None: docker(args, receipt['docker']['context'], transport_environment))
    with pytest.raises(UnsafeTestTarget):
        environment.create()
    receipt = provenance.read_receipt(provenance.receipt_path(RUN))
    assert receipt['state'] == 'failed'
    assert 'container_id' not in receipt
    assert not any(args[0] in {'stop', 'rm'} or args[:2] == ['network', 'rm'] for args in seen)
    if failure != 'run':
        assert not any(args[:2] == ['network', 'create'] for args in seen)


def test_docker_endpoint_overrides_refused_without_subprocess(monkeypatch):
    monkeypatch.setenv('DOCKER_HOST', 'ssh://existing-host')
    run = MagicMock(side_effect=AssertionError('Subprocess forbidden'))
    monkeypatch.setattr(provenance.subprocess, 'run', run)
    with pytest.raises(UnsafeTestTarget):
        provenance.docker(['info'])
    run.assert_not_called()


def test_remote_context_refused(monkeypatch):
    docker = MagicMock(side_effect=['remote', json.dumps([{'Endpoints': {'docker': {'Host': 'tcp://remote:2376'}}}])])
    monkeypatch.setattr(provenance, 'docker', docker)
    with pytest.raises(UnsafeTestTarget):
        provenance.endpoint()
    assert docker.call_count == 2


def test_busy_local_port_does_not_create_socket_listener(monkeypatch):
    reservation = MagicMock()
    reservation.__enter__.return_value.bind.side_effect = OSError('Address already in use')
    monkeypatch.setattr(provenance.socket, 'socket', lambda *args: reservation)
    with pytest.raises(UnsafeTestTarget):
        with provenance.available_port():
            pytest.fail('Occupied port must not be accepted')
    reservation.__enter__.return_value.listen.assert_not_called()


def test_cleanup_uses_receipt_ids_and_checks_empty_network(setup, monkeypatch):
    target, receipt, path = setup
    monkeypatch.setattr(environment, 'require_operation', lambda *args: receipt)
    monkeypatch.setattr(environment, 'verify_resources', lambda *args: receipt)
    engine = MagicMock()
    monkeypatch.setattr(TestTarget, 'engine', lambda *args: engine)
    docker = MagicMock(side_effect=['', '', json.dumps([network_info(receipt, [])]), ''])
    monkeypatch.setattr(environment, 'docker', docker)
    monkeypatch.setattr(environment, 'docker_mutation', lambda args, receipt, transport_environment=None: docker(args, receipt['docker']['context'], transport_environment))
    environment.cleanup(target)
    assert [call.args[0] for call in docker.call_args_list] == [
        ['stop', '--time=10', CONTAINER], ['rm', CONTAINER], ['network', 'inspect', NETWORK], ['network', 'rm', NETWORK]]


def test_cleanup_cannot_stop_on_failed_provenance(setup, monkeypatch):
    target, receipt, path = setup
    monkeypatch.setattr(environment, 'require_operation', lambda *args: receipt)
    monkeypatch.setattr(environment, 'verify_resources', MagicMock(side_effect=UnsafeTestTarget('mismatch')))
    docker = MagicMock(side_effect=AssertionError('No destructive action allowed'))
    monkeypatch.setattr(environment, 'docker', docker)
    monkeypatch.setattr(environment, 'docker_mutation', lambda args, receipt, transport_environment=None: docker(args, receipt['docker']['context'], transport_environment))
    with pytest.raises(UnsafeTestTarget):
        environment.cleanup(target)
    docker.assert_not_called()


def test_successful_creation_receipt_is_persisted_before_start(tmp_path, monkeypatch):
    monkeypatch.setattr(provenance, 'ROOT', tmp_path)
    monkeypatch.setenv('RUN_ISOLATED_POSTGRES_TESTS', '1')
    for name in ('PG_TEST_RECEIPT', 'PG_TEST_RUN_ID', 'PG_TEST_PASSWORD', 'PG_TEST_OPERATION_NONCE'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(environment.secrets, 'token_hex', lambda count: RUN if count == 16 else SECRET)
    monkeypatch.setattr(environment, 'endpoint', lambda: ENDPOINT)
    @contextmanager
    def port():
        yield
    monkeypatch.setattr(environment, 'available_port', port)
    verified = []
    monkeypatch.setattr(environment, 'verify_resources', lambda *args, **kwargs: verified.append(kwargs.get('running', True)))
    seen = []
    def docker(args, *unused):
        seen.append(args)
        receipt = json.loads(provenance.receipt_path(RUN).read_text())
        if args[:2] in (['container', 'ls'], ['network', 'ls']):
            return ''
        if args[:2] == ['image', 'inspect']:
            return json.dumps([{'Id': IMAGE, 'Config': {'Volumes': {provenance.DATA: {}}, 'Env': [f'PGDATA={provenance.DATA}']}}])
        if args[:2] == ['network', 'create']:
            return NETWORK
        if args[:2] == ['network', 'inspect']:
            return json.dumps([network_info(receipt, [])])
        if args[0] == 'create':
            return CONTAINER
        if args[:2] == ['inspect', '--type=container']:
            return json.dumps([{'Created': datetime.now(UTC).isoformat()}])
        if args[0] == 'start':
            assert receipt['container_id'] == CONTAINER
            assert receipt['state'] == 'creating'
            assert args == ['start', CONTAINER]
            return CONTAINER
        if args[:2] == ['inspect', '--format']:
            return 'healthy'
        raise AssertionError('Unexpected command')
    monkeypatch.setattr(environment, 'docker', docker)
    monkeypatch.setattr(environment, 'docker_mutation', lambda args, receipt, transport_environment=None: docker(args, receipt['docker']['context'], transport_environment))
    path = environment.create()
    receipt = provenance.read_receipt(path)
    assert receipt['state'] == 'created'
    assert receipt['container_id'] == CONTAINER
    assert verified == [False, True]
    assert not any(args[0] in {'stop', 'rm'} for args in seen)
    network_creates = [
        args for args in seen
        if args[:2] == ['network', 'create']
        ]
    assert len(network_creates) == 1
    assert '--internal' in network_creates[0]
    assert '--driver' in network_creates[0]
    assert 'bridge' in network_creates[0]
    # A repeated create with the same generated run must fail before any Docker call.
    before = len(seen)
    with pytest.raises(FileExistsError):
        environment.create()
    assert len(seen) == before


def test_provision_cannot_connect_before_receipt_resources_verified(setup, monkeypatch):
    target, receipt, path = setup
    verify = MagicMock(side_effect=UnsafeTestTarget('wrong endpoint or mounts'))
    monkeypatch.setattr(environment, 'verify_resources', verify)
    connect = MagicMock(side_effect=AssertionError('No database access permitted'))
    monkeypatch.setattr(TestTarget, 'engine', connect)
    with pytest.raises(UnsafeTestTarget):
        environment.operate('provision', path)
    connect.assert_not_called()
    assert provenance.read_receipt(path)['state'] == 'failed'


def test_no_opt_in_means_no_docker_even_with_valid_receipt(setup, monkeypatch):
    target, receipt, path = setup
    monkeypatch.setenv('RUN_ISOLATED_POSTGRES_TESTS', '0')
    verify = MagicMock(side_effect=AssertionError('No Docker allowed'))
    monkeypatch.setattr(environment, 'verify_resources', verify)
    with pytest.raises(UnsafeTestTarget):
        environment.operate('provision', path)
    verify.assert_not_called()
    assert provenance.read_receipt(path)['state'] == 'created'


def test_cleanup_requires_its_own_operation_lease(setup, monkeypatch):
    target, receipt, path = setup
    receipt.update(state='testing', operation_nonce='nonce', operator=provenance.process_identity())
    path.write_text(json.dumps(receipt))
    monkeypatch.setenv('PG_TEST_RECEIPT', str(path))
    monkeypatch.setenv('PG_TEST_OPERATION_NONCE', 'nonce')
    verify = MagicMock(side_effect=AssertionError('Must reject before any Docker call'))
    monkeypatch.setattr(environment, 'verify_resources', verify)
    with pytest.raises(UnsafeTestTarget):
        environment.cleanup(target)
    verify.assert_not_called()


def test_read_receipt_registers_secret_even_if_receipt_later_unavailable(setup):
    target, receipt, path = setup
    provenance.read_receipt(path)
    path.unlink()
    from scripts.postgres_test_reporting import redact
    assert SECRET not in redact('prefix' + SECRET + 'suffix')


@pytest.mark.parametrize('kind', ['context', 'endpoint', 'daemon_id', 'pinned_daemon'])
def test_mutation_rechecks_endpoint_and_refuses_changes(setup, monkeypatch, kind):
    target, receipt, path = setup
    actual = dict(ENDPOINT)
    if kind != 'pinned_daemon':
        actual[kind] = 'unexpected'
    monkeypatch.setattr(provenance, 'endpoint', lambda: actual)
    transport = MagicMock(return_value='unexpected-daemon')
    monkeypatch.setattr(provenance, 'docker', transport)
    with pytest.raises(UnsafeTestTarget):
        provenance.docker_mutation(['start', CONTAINER], receipt)
    assert not any(call.args[0][0] == 'start' for call in transport.call_args_list)


def test_mutation_uses_pinned_unix_endpoint_not_context(setup, monkeypatch):
    target, receipt, path = setup
    monkeypatch.setattr(provenance, 'endpoint', lambda: ENDPOINT)
    transport = MagicMock(side_effect=[ENDPOINT['daemon_id'], CONTAINER])
    monkeypatch.setattr(provenance, 'docker', transport)
    assert provenance.docker_mutation(['start', CONTAINER], receipt) == CONTAINER
    assert all(call.kwargs.get('address') == ENDPOINT['endpoint'] for call in transport.call_args_list)
    assert all('context' not in call.kwargs for call in transport.call_args_list)


def test_docker_transport_pins_host_argument_without_context(monkeypatch):
    for name in ('DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH'):
        monkeypatch.delenv(name, raising=False)
    run = MagicMock(return_value=MagicMock(stdout='synthetic-daemon'))
    monkeypatch.setattr(provenance.subprocess, 'run', run)
    provenance.docker(['info'], address=ENDPOINT['endpoint'])
    assert run.call_args.args[0] == ['docker', '--host', ENDPOINT['endpoint'], 'info']


def test_failed_creation_reports_only_private_receipt_locator(setup, monkeypatch, capsys):
    target, receipt, path = setup
    monkeypatch.setattr(provenance, 'ROOT', path.parent / 'recovery-root')
    provenance.ROOT.mkdir()
    monkeypatch.setattr(environment.secrets, 'token_hex', lambda count: RUN if count == 16 else SECRET)
    monkeypatch.setattr(environment, 'endpoint', MagicMock(side_effect=UnsafeTestTarget(SECRET)))
    with pytest.raises(UnsafeTestTarget):
        environment.create()
    output = capsys.readouterr()
    assert 'private recovery receipt:' in output.err
    assert str(provenance.receipt_path(RUN)) in output.err
    assert SECRET not in output.err + output.out


def test_actual_runner_uses_sanitized_pytest_environment(setup, monkeypatch):
    target, receipt, path = setup
    monkeypatch.setattr(environment, 'require_operation', lambda *args: receipt)
    monkeypatch.setenv('PYTEST_PLUGINS', 'unapproved_reporter')
    monkeypatch.setenv('PYTEST_DEBUG', '1')
    monkeypatch.setenv('PYTEST_ADDOPTS', '--debug --log-file=unsafe.log')
    run = MagicMock(return_value=MagicMock(stdout='', stderr='', returncode=3))
    monkeypatch.setattr(environment.subprocess, 'run', run)
    assert environment.run_tests(target) == 3
    assert 'addopts=' in run.call_args.args[0]
    assert 'scripts.postgres_test_reporting' in run.call_args.args[0]
    child = run.call_args.kwargs['env']
    assert 'PYTEST_PLUGINS' not in child
    assert 'PYTEST_DEBUG' not in child
    assert child['PYTEST_ADDOPTS'] == ''
    assert child['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] == '1'


@pytest.mark.parametrize('exit_code', [0, 1, 2, 3, 4, 5])
def test_completed_pytest_exit_codes_are_propagated(setup, monkeypatch, exit_code):
    target, receipt, path = setup
    receipt['state'] = 'validated'
    path.write_text(json.dumps(receipt))
    monkeypatch.setattr(environment, 'verify_resources', lambda *args: receipt)
    monkeypatch.setattr(environment, 'run_tests', lambda *args: exit_code)
    assert environment.operate('test', path) == exit_code
    assert provenance.read_receipt(path)['state'] == 'tested'
