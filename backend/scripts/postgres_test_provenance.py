"""Private creation receipts and live Docker identity checks. No import-time I/O."""
from contextlib import contextmanager
from datetime import datetime
import fcntl
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import time

from scripts.postgres_test_harness import TestTarget, UnsafeTestTarget

ROOT = Path('/tmp')
TTL_SECONDS = 7200
IMAGE = 'postgres:17-bookworm'
DATA = '/var/lib/postgresql/data'
OPTIONS = {'com.docker.network.bridge.enable_icc': 'false',
           'com.docker.network.bridge.host_binding_ipv4': '127.0.0.1'}
ACTIVE = {'provisioning', 'validating', 'testing', 'cleaning'}
BROWSER_MAX_SECONDS = 900


def refuse():
    raise UnsafeTestTarget('Isolated creation provenance verification failed')


def docker(args, context=None, environment=None, address=None):
    """Never inherit remote endpoints; capture output, including inspect secrets."""
    if any(key in os.environ for key in ('DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH')):
        refuse()
    if address is not None and (context is not None or not address.startswith('unix:///')):
        refuse()
    selector = ['--host', address] if address is not None else (['--context', context] if context else [])
    command = ['docker'] + selector + list(args)
    try:
        return subprocess.run(command, env=environment, capture_output=True, text=True,
                              check=True, timeout=30).stdout.strip()
    except Exception:
        raise UnsafeTestTarget('Isolated Docker operation failed; manual review required') from None


def endpoint():
    context = docker(['context', 'show'])
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', context):
        refuse()
    result = json.loads(docker(['context', 'inspect', context]))[0]
    address = result['Endpoints']['docker']['Host']
    # Local Unix sockets only: no SSH, TCP, remote daemons or endpoint defaults.
    if not address.startswith('unix:///'):
        refuse()
    daemon = docker(['info', '--format', '{{.ID}}'], context)
    if not daemon:
        refuse()
    return {'context': context, 'endpoint': address, 'daemon_id': daemon}



def docker_mutation(args, receipt, environment=None):
    """Revalidate immediately, then bypass mutable context resolution for writes."""
    expected = receipt['docker']
    if endpoint() != expected:
        refuse()
    address = expected['endpoint']
    if docker(['info', '--format', '{{.ID}}'], address=address) != expected['daemon_id']:
        refuse()
    return docker(args, environment=environment, address=address)


def process_identity(pid=None):
    pid = os.getpid() if pid is None else pid
    try:
        boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        return {'pid': pid, 'boot_id': boot, 'start_ticks': fields[19]}
    except (OSError, ValueError, IndexError):
        raise UnsafeTestTarget('Operation lease process is unavailable') from None


def receipt_path(run_id):
    return ROOT / f'jobtracker-pg613-{run_id}' / 'receipt.json'


def check_file(path):
    path = Path(path)
    # No symlink resolution/adoption, including the private parent directory.
    if not path.is_absolute() or path.parent.parent != ROOT or path.name != 'receipt.json':
        refuse()
    parent = path.parent.lstat()
    info = path.lstat()
    if (not stat.S_ISDIR(parent.st_mode) or stat.S_IMODE(parent.st_mode) != 0o700
            or parent.st_uid != os.getuid() or not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.getuid()
            or info.st_nlink != 1 or info.st_size > 32768):
        refuse()
    return path


def read_receipt(path=None):
    try:
        path = check_file(path or os.environ.get('PG_TEST_RECEIPT', ''))
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor) as stream:
            receipt = json.load(stream)
        target = TestTarget(receipt['run_id'], receipt['password'])
        if (path != receipt_path(target.run_id) or receipt['version'] != 1
                or not 0 <= time.time() - receipt['issued_at'] <= TTL_SECONDS
                or receipt['approved'] != approved_configuration(target)):
            refuse()
        if receipt['state'] not in {'creating', 'failed'}:
            if (not re.fullmatch(r'[0-9a-f]{64}', receipt['container_id'])
                    or not re.fullmatch(r'[0-9a-f]{64}', receipt['network_id'])
                    or not re.fullmatch(r'sha256:[0-9a-f]{64}', receipt['image_id'])):
                refuse()
            created = datetime.fromisoformat(receipt['container_created'].replace('Z', '+00:00')).timestamp()
            if not receipt['issued_at'] - 2 <= created <= time.time() + 2:
                refuse()
        from scripts.postgres_test_reporting import register_secret
        register_secret(receipt['password'])
        return receipt
    except UnsafeTestTarget:
        raise
    except Exception:
        raise UnsafeTestTarget('Missing, stale or invalid isolated creation receipt') from None


def approved_configuration(target):
    return {'name': target.container, 'role': target.role,
            'databases': [target.database(case) for case in ('api', 'fresh', 'legacy', 'bigint', 'char', 'timestamp')],
            'host': '127.0.0.1', 'port': 55433, 'pgdata': DATA, 'tmpfs': 'rw,nosuid,size=1g',
            'image_tag': IMAGE, 'network_name': target.container + '-net', 'network_options': OPTIONS}


def write_receipt(stream, receipt):
    stream.seek(0)
    json.dump(receipt, stream)
    stream.truncate()
    stream.flush()
    os.fsync(stream.fileno())


def new_receipt(target):
    path = receipt_path(target.run_id)
    path.parent.mkdir(mode=0o700)  # Never reuse even an empty pre-existing directory.
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    return path, os.fdopen(descriptor, 'r+')


@contextmanager
def locked_receipt(path):
    path = check_file(path)
    descriptor = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
    with os.fdopen(descriptor, 'r+') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            refuse()
        yield stream, read_receipt(path)


def _authorize_receipt(target, allowed_states):
    if os.environ.get('RUN_ISOLATED_POSTGRES_TESTS') != '1':
        refuse()
    receipt = read_receipt()
    if (receipt['run_id'] != target.run_id or receipt['password'] != target.password
            or receipt['state'] not in allowed_states
            or receipt.get('operation_nonce') != os.environ.get('PG_TEST_OPERATION_NONCE')
            or not receipt.get('operation_nonce')
            or process_identity(receipt['operator']['pid']) != receipt['operator']):
        refuse()
    return receipt


def authorize_receipt(target):
    return _authorize_receipt(target, ACTIVE)


def authorize_browser_receipt(target):
    """Offline-only lease check; does not grant existing harness capabilities."""
    receipt = _authorize_receipt(target, {"browser-integration"})
    if not re.fullmatch(r"[0-9a-f]{32}", receipt["operation_nonce"]):
        refuse()
    now = time.time()
    started = receipt.get('browser_started_at')
    expires = receipt.get('browser_expires_at')
    deadline = receipt.get('browser_monotonic_deadline')
    if (type(started) not in (int, float) or type(expires) not in (int, float)
            or type(deadline) not in (int, float)
            or not receipt['issued_at'] <= started <= now < expires
            or not 0 < expires - started <= BROWSER_MAX_SECONDS
            or expires > receipt['issued_at'] + TTL_SECONDS
            or not time.monotonic() < deadline
            or receipt.get('browser_database') != target.database('api')):
        refuse()
    return receipt


def require_operation(target, state):
    receipt = authorize_receipt(target)
    if receipt['state'] != state:
        refuse()
    return receipt


def validate_container(target, receipt, information, running=True):
    config, host = information.get('Config', {}), information.get('HostConfig', {})
    labels, environment = config.get('Labels') or {}, config.get('Env') or []
    networks = information.get('NetworkSettings', {}).get('Networks', {})
    ports = {'5432/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '55433'}]}
    if (information.get('Id') != receipt['container_id'] or information.get('Image') != receipt['image_id']
            or information.get('Name') != '/' + target.container
            or information.get('Created') != receipt['container_created']
            or labels.get('jobtracker.test-run') != target.run_id
            or labels.get('jobtracker.receipt') != receipt['receipt_nonce']
            or labels.get('jobtracker.disposable') != 'true'
            or not {f'PGDATA={DATA}', f'POSTGRES_USER={target.role}',
                    f'POSTGRES_DB={target.database("api")}', f'POSTGRES_PASSWORD={target.password}'} <= set(environment)
            or config.get('Cmd') != ['postgres', '-c', f'jobtracker.test_run={target.run_id}']
            or host.get('PortBindings') != ports
            or (running and information.get('NetworkSettings', {}).get('Ports') != ports)
            or host.get('Tmpfs') != {DATA: 'rw,nosuid,size=1g'}
            or any(mount.get('Type') != 'tmpfs' or mount.get('Destination') != DATA
                   for mount in information.get('Mounts', []))
            or any(host.get(key) for key in ('Binds', 'VolumesFrom', 'Privileged', 'CapAdd', 'Devices', 'ExtraHosts', 'Links'))
            or host.get('PidMode') or host.get('NetworkMode') != receipt['network_id']
            or (running and set(networks) != {receipt['approved']['network_name']})
            or (running and networks[receipt['approved']['network_name']].get('NetworkID') != receipt['network_id'])
            or information.get('State', {}).get('Running') is not running):
        refuse()


def validate_network(receipt, information, members):
    labels = information.get('Labels') or {}
    if (information.get('Id') != receipt['network_id']
            or information.get('Name') != receipt['approved']['network_name']
            or information.get('Driver') != 'bridge' or information.get('Scope') != 'local'
            or information.get('Internal') is not True or information.get('EnableIPv6') is not False
            or information.get('Attachable') or information.get('Ingress')
            or information.get('Options') not in (
                OPTIONS,
                {
                    **OPTIONS,
                    'com.docker.network.enable_ipv4': 'true',
                    'com.docker.network.enable_ipv6': 'false',
                },
            )
            or labels.get('jobtracker.test-run') != receipt['run_id']
            or labels.get('jobtracker.receipt') != receipt['receipt_nonce']
            or set(information.get('Containers') or {}) != set(members)):
        refuse()


def verify_resources(target, receipt=None, running=True):
    receipt = authorize_receipt(target) if receipt is None else receipt
    if endpoint() != receipt['docker']:
        refuse()
    context = receipt['docker']['context']
    container = json.loads(docker(['inspect', '--type=container', receipt['container_id']], context))[0]
    validate_container(target, receipt, container, running)
    network = json.loads(docker(['network', 'inspect', receipt['network_id']], context))[0]
    validate_network(receipt, network, [receipt['container_id']] if running else [])
    return receipt


@contextmanager
def available_port():
    # Hold the reservation through network creation. Docker provides the final
    # atomic port check after release; any race/failure leaves a failed receipt.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
        try:
            reservation.bind(('127.0.0.1', 55433))
        except OSError:
            raise UnsafeTestTarget('Disposable test port is occupied; refusing creation') from None
        yield
