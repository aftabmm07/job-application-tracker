"""Offline preflight only. No engine, Docker inspection, SQL, or lease issuance."""
import os

from app.database_config import runtime_database_mode, validate_database_name
from scripts.postgres_test_harness import TestTarget, UnsafeTestTarget
from scripts.postgres_test_provenance import authorize_browser_receipt


def browser_target_configuration():
    """Return a receipt-derived URL, not authorization to connect or serve.

    The future operation must issue its own exclusive lease and perform live
    provenance/server checks. Existing harness operations cannot issue this lease.
    Use the process environment consistently with the existing lease validator.
    """
    try:
        if runtime_database_mode(os.environ) != 'disposable':
            raise UnsafeTestTarget('Disposable mode required')
        if os.environ.get('APPROVE_BROWSER_INTEGRATION') != '1':
            raise UnsafeTestTarget('Explicit browser integration approval required')
        # Never accept ambient credentials, ordinary targets, or libpq defaults.
        conflicts = {'POSTGRES_PASSWORD', 'DATABASE_URL', 'APP_DATABASE_NAME',
                     'APP_DATABASE_HOST', 'APP_DATABASE_PORT', 'APP_DATABASE_USER'}
        allowed_pg = {'PG_TEST_RECEIPT', 'PG_TEST_OPERATION_NONCE'}
        if any(key in conflicts or key.startswith('MIGRATION_DATABASE_')
               or (key.startswith('PG') and key not in allowed_pg)
               for key in os.environ):
            raise UnsafeTestTarget('Conflicting database settings')
        if not os.environ.get('PG_TEST_RECEIPT'):
            raise UnsafeTestTarget('Creation receipt required')
        target = TestTarget.from_environment()
        from scripts.postgres_test_provenance import read_receipt
        receipt = read_receipt()
        # Explicit approval names exactly this run's API database, not any of the
        # disposable migration-test databases. No fallback or caller credentials.
        database = validate_database_name(os.environ.get('BROWSER_DATABASE_NAME'), 'BROWSER_DATABASE_NAME')
        if database != target.database('api') or database not in receipt['approved']['databases']:
            raise UnsafeTestTarget('Browser database identity mismatch')
        url = target.url('api')
        target.validate_url(url)
        if (url.host != receipt['approved']['host'] or url.port != receipt['approved']['port']
                or url.username != receipt['approved']['role'] or url.database != database):
            raise UnsafeTestTarget('Browser connection identity mismatch')
        return target, url
    except Exception:
        # Includes malformed receipt/lease structures and supplied values.
        raise UnsafeTestTarget('Browser database preflight refused') from None


def validate_browser_database():
    """Validate configuration and the current bounded browser-operation lease."""
    try:
        target, url = browser_target_configuration()
        authorize_browser_receipt(target)
        return url
    except Exception:
        raise UnsafeTestTarget('Browser database preflight refused') from None
