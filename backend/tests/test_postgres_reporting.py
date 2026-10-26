"""Intentionally fail a child pytest process using ONLY synthetic secrets."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.postgres_test_reporting import redact, register_secret


DB_PASSWORD = 'synthetic-database-password-for-offline-test'
TOKEN = 'S' * 43


def test_redactor_masks_urls_dictionaries_cookie_headers_and_bearers():
    register_secret(DB_PASSWORD)
    samples = [DB_PASSWORD, f'postgresql+psycopg://u:{DB_PASSWORD}@127.0.0.1:55433/db',
               "{'password': 'unregistered-value'}", f"Cookie: session={TOKEN}",
               f"Headers({{'set-cookie': 'session={TOKEN}; HttpOnly'}})", TOKEN, 'a' * 64]
    for sample in samples:
        result = redact(sample)
        assert DB_PASSWORD not in result
        assert TOKEN not in result
        assert 'unregistered-value' not in result
        assert 'postgresql+psycopg://' not in result
        assert 'a' * 64 not in result


def test_intentional_failure_reports_do_not_expose_synthetic_secrets(tmp_path):
    test = tmp_path / 'test_intentional_failure.py'
    test.write_text('''import os
import logging

def test_failure():
    password = os.environ['SYNTHETIC_PASSWORD']
    token = 'S' * 43
    print(password)
    print('Cookie: session=' + token)
    logging.warning('postgresql://u:%s@127.0.0.1:55433/db', password)
    raise RuntimeError("connection failed with password=" + repr(password) + " bearer=" + token)
''')
    environment = dict(os.environ, SYNTHETIC_PASSWORD=DB_PASSWORD, PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',
                       PYTEST_ADDOPTS='', PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    # No receipt or opt-in: no PostgreSQL or Docker operations exist in this child.
    environment.pop('PG_TEST_RECEIPT', None)
    report = tmp_path / 'report.xml'
    result = subprocess.run([sys.executable, '-m', 'pytest', '-c', '/dev/null', '-p',
                             'scripts.postgres_test_reporting', '--isolated-safe-reports', '--showlocals',
                             '--tb=long', '-s', '--log-cli-level=WARNING', '--junitxml', str(report), str(test)],
                            env=environment, capture_output=True, text=True, timeout=30)
    assert result.returncode == 1
    combined = result.stdout + result.stderr + report.read_text()
    assert DB_PASSWORD not in combined
    assert TOKEN not in combined
    assert 'postgresql://u:' not in combined
    assert '[REDACTED' in combined


def test_inherited_plugins_debugging_and_addopts_are_removed_before_pytest(tmp_path):
    from scripts.postgres_test_reporting import safe_pytest_environment
    marker = tmp_path / 'unapproved-plugin-ran'
    (tmp_path / 'unapproved_reporter.py').write_text(
        "import os\nfrom pathlib import Path\n"
        "Path(os.environ['PLUGIN_MARKER']).write_text(os.environ['SYNTHETIC_PASSWORD'])\n")
    test = tmp_path / 'test_safe.py'
    test.write_text('def test_safe():\n    assert True\n')
    environment = safe_pytest_environment(dict(os.environ,
        PYTEST_PLUGINS='unapproved_reporter', PYTEST_DEBUG='1', PYTEST_ADDOPTS='--debug --showlocals -s',
        PYTHONPATH=os.pathsep.join([str(tmp_path), str(Path(__file__).resolve().parents[1])]),
        PLUGIN_MARKER=str(marker), SYNTHETIC_PASSWORD=DB_PASSWORD))
    environment.pop('PG_TEST_RECEIPT', None)
    assert 'PYTEST_PLUGINS' not in environment
    assert 'PYTEST_DEBUG' not in environment
    result = subprocess.run([sys.executable, '-m', 'pytest', '-c', '/dev/null', '-p',
                             'scripts.postgres_test_reporting', '--isolated-safe-reports', str(test)],
                            env=environment, cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0
    assert not marker.exists()
    assert not (tmp_path / 'pytestdebug.log').exists()
    assert DB_PASSWORD not in result.stdout + result.stderr


@pytest.mark.parametrize('mode', ['ini', 'cli'])
def test_cli_and_ini_file_logging_cannot_write_synthetic_secrets(tmp_path, mode):
    from scripts.postgres_test_reporting import safe_pytest_environment
    ini_log, cli_log = tmp_path / 'ini.log', tmp_path / 'cli.log'
    cli_log.write_text('existing safe content')
    config = tmp_path / 'pytest.ini'
    config.write_text(f'[pytest]\nlog_file = {ini_log}\nlog_file_level = WARNING\n')
    test = tmp_path / 'test_logging.py'
    test.write_text('''import logging
import os

def test_logging():
    password = os.environ['SYNTHETIC_PASSWORD']
    logging.warning('password=%s', password)
    logging.warning('postgresql://u:%s@127.0.0.1:55433/db', password)
    logging.warning('Cookie: session=%s', 'S' * 43)
    assert False, 'Intentional synthetic-secret failure'
''')
    environment = safe_pytest_environment(dict(os.environ, SYNTHETIC_PASSWORD=DB_PASSWORD,
        PYTHONPATH=str(Path(__file__).resolve().parents[1])))
    environment.pop('PG_TEST_RECEIPT', None)
    command = [sys.executable, '-m', 'pytest', '-c', str(config), '-p',
               'scripts.postgres_test_reporting', '--isolated-safe-reports']
    if mode == 'cli':
        command += ['--log-file', str(cli_log), '--log-file-level=WARNING']
    result = subprocess.run(command + [str(test)], env=environment,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 1
    assert not ini_log.exists()
    assert cli_log.read_text() == 'existing safe content'
    assert DB_PASSWORD not in result.stdout + result.stderr
    assert TOKEN not in result.stdout + result.stderr
    assert 'postgresql://u:' not in result.stdout + result.stderr


def test_debug_file_is_rejected_before_it_can_be_opened(tmp_path):
    from scripts.postgres_test_reporting import safe_pytest_environment
    debug_file = tmp_path / 'debug.log'
    debug_file.write_text('existing safe diagnostic')
    test = tmp_path / 'test_safe.py'
    test.write_text('def test_safe():\n    assert True\n')
    environment = safe_pytest_environment(dict(os.environ,
        SYNTHETIC_PASSWORD=DB_PASSWORD, PYTHONPATH=str(Path(__file__).resolve().parents[1])))
    environment.pop('PG_TEST_RECEIPT', None)
    result = subprocess.run([sys.executable, '-m', 'pytest', '-c', '/dev/null', '-p',
                             'scripts.postgres_test_reporting', '--isolated-safe-reports',
                             f'--debug={debug_file}', str(test)], env=environment,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 4
    assert debug_file.read_text() == 'existing safe diagnostic'
    assert DB_PASSWORD not in result.stdout + result.stderr
