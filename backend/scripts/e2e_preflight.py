"""Offline validation of one operator-approved E2E settings file; prints no values."""

import os
from pathlib import Path
import re
import stat

PROTECTED_RUN_IDS = frozenset({"06479b7b1e0fc5d4", "d3a273dcf94789a9"})
KEYS = frozenset({
    "E2E_RUN_ID", "E2E_FRONTEND_PORT", "E2E_MAIL_UI_PORT",
    "E2E_POSTGRES_PASSWORD", "E2E_CSRF_HMAC_SECRET",
})
INHERITED_OVERRIDES = frozenset({
    "COMPOSE_PROJECT_NAME", "COMPOSE_FILE", "COMPOSE_PROFILES", "STACK_RUN_ID",
    "STACK_POSTGRES_PASSWORD", "STACK_CSRF_HMAC_SECRET", "DATABASE_URL",
    "POSTGRES_PASSWORD", "PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD",
})


def validate_settings(values, run_id):
    if not re.fullmatch(r"[0-9a-f]{16}", run_id or "") or run_id in PROTECTED_RUN_IDS:
        raise ValueError("E2E run identity is invalid or reserved")
    if set(values) != KEYS or values["E2E_RUN_ID"] != run_id:
        raise ValueError("E2E settings do not match the approved run")
    ports = []
    for name in ("E2E_FRONTEND_PORT", "E2E_MAIL_UI_PORT"):
        raw = values[name]
        if not re.fullmatch(r"[1-9][0-9]{4}", raw) or not 20000 <= int(raw) <= 60999:
            raise ValueError("E2E port must be an approved high loopback port")
        ports.append(int(raw))
    if ports[0] == ports[1]:
        raise ValueError("E2E ports must differ")
    for name in ("E2E_POSTGRES_PASSWORD", "E2E_CSRF_HMAC_SECRET"):
        value = values[name]
        if (len(value.encode()) < 32 or len(value) > 1024
                or any(ord(char) < 33 or ord(char) > 126 for char in value)):
            raise ValueError("E2E private credentials are invalid")


def main():
    if INHERITED_OVERRIDES.intersection(os.environ):
        raise SystemExit("Inherited stack or database override is present")
    run_id = os.environ.get("E2E_RUN_ID", "")
    if not re.fullmatch(r"[0-9a-f]{16}", run_id) or run_id in PROTECTED_RUN_IDS:
        raise SystemExit("E2E settings preflight failed")
    raw_path = os.environ.get("E2E_ENV_FILE")
    if not raw_path:
        raise SystemExit("E2E settings preflight failed")
    path = Path(raw_path)
    expected = Path.home() / ".config" / "jobtracker-e2e" / f"{run_id}.env"
    try:
        if (path != expected or path.is_symlink() or not path.is_file()
                or path.stat().st_uid != os.getuid()
                or stat.S_IMODE(path.stat().st_mode) != 0o600
                or path.parent.is_symlink() or path.parent.stat().st_uid != os.getuid()
                or stat.S_IMODE(path.parent.stat().st_mode) != 0o700):
            raise ValueError
        lines = [line.partition("=") for line in path.read_text().splitlines()]
        values = {name: value for name, delimiter, value in lines if delimiter}
        if len(lines) != len(KEYS) or len(values) != len(KEYS):
            raise ValueError
        validate_settings(values, run_id)
        for name in KEYS - {"E2E_RUN_ID"}:
            if name in os.environ:
                raise ValueError
    except (OSError, UnicodeError, ValueError):
        raise SystemExit("E2E settings preflight failed") from None
    print("E2E settings verified; values withheld")


if __name__ == "__main__":
    main()
