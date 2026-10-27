"""Defence-in-depth report redaction for the receipt-bound pytest runner."""
import os
import re

import pytest

_SECRETS = set()


def register_secret(value):
    if isinstance(value, str) and len(value) >= 8:
        _SECRETS.add(value)


def redact(value):
    value = str(value)
    secrets = set(_SECRETS)
    secrets.update(value for name, value in os.environ.items()
                   if any(word in name.upper() for word in ('PASSWORD', 'SECRET', 'TOKEN')) and len(value) >= 8)
    # Also works for the parent runner after subprocess completion, without
    # exposing receipt contents or depending on the child's in-memory registry.
    path = os.environ.get('PG_TEST_RECEIPT')
    if path:
        try:
            from scripts.postgres_test_provenance import read_receipt
            secrets.add(read_receipt(path)['password'])
        except Exception:
            pass
    for secret in sorted(secrets, key=len, reverse=True):
        if secret:
            value = value.replace(secret, '[REDACTED]')
    value = re.sub(r"postgres(?:ql)?(?:\+[a-z0-9_]+)?://[^\s<>\"']+", '[REDACTED DATABASE URL]', value, flags=re.I)
    # Whole cookie lines, including assertion explanations and Headers reprs.
    value = '\n'.join('[REDACTED COOKIE LINE]' if re.search(r"set-cookie|['\"]?cookie['\"]?\s*[:=]", line, re.I)
                      else line for line in value.split('\n'))
    value = re.sub(r"(['\"]?(?:password|passwd|raw_token|csrf_token|csrf_hmac_secret)['\"]?\s*[:=]\s*)"
                   r"(?:'[^']*'|\"[^\"]*\"|[^\s,}\]]+)", r'\1[REDACTED]', value, flags=re.I)
    # Session bearer tokens and SHA-256/CSRF-sized hex, even with no field label.
    value = re.sub(r'(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{43}(?![A-Za-z0-9_-])', '[REDACTED TOKEN]', value)
    return re.sub(r'(?<![a-fA-F0-9])[a-fA-F0-9]{64}(?![a-fA-F0-9])', '[REDACTED DIGEST]', value)



def safe_pytest_environment(values):
    """Remove pytest injection/debug settings BEFORE Python imports pytest."""
    environment = {name: value for name, value in values.items() if not name.startswith('PYTEST_')}
    environment.update(PYTEST_DISABLE_PLUGIN_AUTOLOAD='1', PYTEST_ADDOPTS='')
    return environment


def pytest_addoption(parser):
    parser.addoption('--isolated-safe-reports', action='store_true', default=False)



@pytest.hookimpl(tryfirst=True)
def pytest_load_initial_conftests(early_config, parser, args):
    # The approved runner explicitly loads this plugin with -p. Reject tracing
    # before pytest_cmdline_parse can open/truncate a --debug file.
    if '--isolated-safe-reports' in args:
        if any(argument.split('=', 1)[0] in {'--debug', '--pdb', '--trace'} for argument in args):
            raise pytest.UsageError('Debuggers and debug logs are forbidden for isolated authentication tests')


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config):
    config._isolated_safe_reports = config.getoption('--isolated-safe-reports')
    if not config._isolated_safe_reports:
        return
    if getattr(config.option, 'usepdb', False) or getattr(config.option, 'trace', False) or config.option.debug:
        raise pytest.UsageError('Debuggers and debug logs are forbidden for isolated authentication tests')
    config.option.showlocals = False
    config.option.fulltrace = False
    config.option.tbstyle = 'short'
    config.option.capture = 'fd'
    manager = config.pluginmanager.getplugin('capturemanager')
    if manager is not None and manager._method != 'fd':
        # -s is processed before pytest_configure; changing only the option does
        # not replace the already-created capture manager.
        manager.stop_global_capturing()
        manager._method = 'fd'
        manager.start_global_capturing()
    config.option.log_cli_level = None
    config._inicache['log_cli'] = False
    # Disable built-in file logging before LoggingPlugin constructs/opens a file.
    # CLI and ini values must both be overridden (get_option_ini falls back).
    config.option.log_file = os.devnull
    config._inicache['log_file'] = os.devnull


def scrub_report(report):
    if report.longrepr:
        report.longrepr = redact(report.longrepr)
    report.sections = [(redact(title), redact(content)) for title, content in report.sections]
    if hasattr(report, 'user_properties'):
        report.user_properties = [(redact(key), redact(value)) for key, value in report.user_properties]
    report.nodeid = redact(report.nodeid)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    if getattr(item.config, '_isolated_safe_reports', False):
        scrub_report(outcome.get_result())


def pytest_collectreport(report):
    # This hook has no config argument; an active option is recorded below.
    if _ACTIVE:
        scrub_report(report)


_ACTIVE = False


@pytest.hookimpl(trylast=True)
def pytest_sessionstart(session):
    global _ACTIVE
    _ACTIVE = getattr(session.config, '_isolated_safe_reports', False)


def pytest_collection_modifyitems(config, items):
    if getattr(config, '_isolated_safe_reports', False):
        for item in items:
            item._nodeid = redact(item.nodeid)
            item.name = redact(item.name)
