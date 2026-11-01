"""Source-only checks; no Docker, browser, SMTP, or database access."""

from pathlib import Path
import os
import ast
import re

import pytest
import yaml

from app.auth_config import AuthConfigurationError, AuthSettings
from app.database_config import DatabaseConfigurationError, stack_database_target
from app.email_delivery import StackCaptureEmailSender
from scripts import e2e_preflight
from scripts.e2e_preflight import validate_settings


ROOT = Path(__file__).resolve().parents[2]
SOURCE = (ROOT / "compose.e2e.yaml").read_text()
COMPOSE = yaml.safe_load(SOURCE)
RUN = "a1b2c3d4e5f60718"
PORT = "22080"
ORIGIN = f"http://localhost:{PORT}"


def settings_values():
    return {
        "E2E_RUN_ID": RUN, "E2E_FRONTEND_PORT": PORT,
        "E2E_MAIL_UI_PORT": "22025",
        "E2E_POSTGRES_PASSWORD": "p" * 40,
        "E2E_CSRF_HMAC_SECRET": "s" * 40,
    }


@pytest.fixture
def private_preflight(tmp_path, monkeypatch):
    """All file checks stay beneath pytest's temporary directory."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    directory = tmp_path / ".config" / "jobtracker-e2e"
    directory.mkdir(parents=True, mode=0o700)
    directory.chmod(0o700)
    path = directory / f"{RUN}.env"
    path.write_text("".join(f"{key}={value}\n" for key, value in settings_values().items()))
    path.chmod(0o600)
    monkeypatch.setattr(e2e_preflight.os, "environ", {
        "E2E_RUN_ID": RUN, "E2E_ENV_FILE": str(path),
    })
    return path


def test_private_preflight_accepts_exact_file_without_disclosing_values(private_preflight, capsys):
    e2e_preflight.main()
    output = capsys.readouterr()
    assert output.out == "E2E settings verified; values withheld\n"
    assert "p" * 40 not in output.out + output.err
    assert "s" * 40 not in output.out + output.err


@pytest.mark.parametrize("path_value", [None, "", ".", "/tmp/unapproved.env"])
def test_private_preflight_rejects_missing_or_wrong_path(private_preflight, monkeypatch, path_value):
    if path_value is None:
        monkeypatch.delitem(os.environ, "E2E_ENV_FILE")
    else:
        monkeypatch.setitem(os.environ, "E2E_ENV_FILE", path_value)
    with pytest.raises(SystemExit, match="E2E settings preflight failed"):
        e2e_preflight.main()


@pytest.mark.parametrize("run_id", ["", "ABCDEF0123456789", "x" * 16,
                                     "06479b7b1e0fc5d4", "d3a273dcf94789a9"])
def test_private_preflight_rejects_run_before_building_path(private_preflight, monkeypatch, run_id):
    monkeypatch.setitem(os.environ, "E2E_RUN_ID", run_id)
    with pytest.raises(SystemExit, match="E2E settings preflight failed"):
        e2e_preflight.main()


@pytest.mark.parametrize("target", ["file", "directory"])
def test_private_preflight_rejects_wrong_permissions(private_preflight, target):
    item = private_preflight if target == "file" else private_preflight.parent
    item.chmod(0o644 if target == "file" else 0o755)
    with pytest.raises(SystemExit, match="E2E settings preflight failed"):
        e2e_preflight.main()


@pytest.mark.parametrize("target", ["file", "directory"])
def test_private_preflight_rejects_symlinks(private_preflight, tmp_path, target):
    if target == "file":
        alternate = tmp_path / "alternate.env"
        alternate.write_bytes(private_preflight.read_bytes())
        private_preflight.unlink()
        private_preflight.symlink_to(alternate)
    else:
        alternate = tmp_path / "alternate-directory"
        private_preflight.parent.rename(alternate)
        private_preflight.parent.symlink_to(alternate, target_is_directory=True)
    with pytest.raises(SystemExit, match="E2E settings preflight failed"):
        e2e_preflight.main()


@pytest.mark.parametrize("change", [
    "E2E_RUN_ID=incorrect\n", "E2E_FRONTEND_PORT=22080\n",
    "UNKNOWN_KEY=value\n", "not-a-setting\n",
])
def test_private_preflight_rejects_duplicate_or_malformed_settings(private_preflight, change):
    with private_preflight.open("a") as stream:
        stream.write(change)
    with pytest.raises(SystemExit, match="E2E settings preflight failed"):
        e2e_preflight.main()


@pytest.mark.parametrize("name", sorted(e2e_preflight.INHERITED_OVERRIDES | {
    "E2E_FRONTEND_PORT", "E2E_MAIL_UI_PORT", "E2E_POSTGRES_PASSWORD",
    "E2E_CSRF_HMAC_SECRET",
}))
def test_private_preflight_rejects_inherited_overrides(private_preflight, monkeypatch, name):
    monkeypatch.setitem(os.environ, name, "synthetic-override")
    with pytest.raises(SystemExit):
        e2e_preflight.main()


def auth_values():
    return {
        "APP_ENV": "development", "APP_DATABASE_MODE": "stack",
        "STACK_RUN_ID": RUN, "E2E_RUN_ID": RUN,
        "E2E_FRONTEND_PORT": PORT, "EMAIL_DELIVERY_MODE": "stack-capture",
        "AUTH_ALLOWED_ORIGINS": ORIGIN, "VERIFICATION_FRONTEND_ORIGIN": ORIGIN,
        "CSRF_HMAC_SECRET": "s" * 40,
    }


def database_values():
    return {
        "APP_ENV": "development", "APP_DATABASE_MODE": "stack",
        "STACK_RUN_ID": RUN, "E2E_RUN_ID": RUN,
        "STACK_DATABASE_NAME": f"jobtracker_stack_{RUN}",
        "STACK_DATABASE_HOST": "db", "STACK_DATABASE_PORT": "5432",
        "STACK_DATABASE_USER": "jobtracker_stack",
        "STACK_POSTGRES_PASSWORD": "p" * 40,
    }


def test_e2e_compose_has_only_project_scoped_isolated_resources():
    assert COMPOSE["name"] == "jobtracker-e2e-${E2E_RUN_ID:?Approved E2E run ID required}"
    assert set(COMPOSE["services"]) == {"db", "backend", "frontend", "capture"}
    assert set(COMPOSE["volumes"]) == {"e2e_pgdata"}
    assert not COMPOSE["volumes"]["e2e_pgdata"]
    assert set(COMPOSE["networks"]) == {"edge", "private", "mail"}
    assert COMPOSE["networks"]["private"]["internal"] is True
    assert all("container_name" not in item for item in COMPOSE["services"].values())
    assert all("external" not in item for item in COMPOSE["networks"].values())
    assert "jobtracker_db" not in SOURCE and "postgres_data" not in SOURCE
    db, api, web, mail = (COMPOSE["services"][name] for name in
                          ("db", "backend", "frontend", "capture"))
    assert db["image"] == "postgres:17"
    assert db["volumes"] == ["e2e_pgdata:/var/lib/postgresql/data"]
    assert db["networks"] == ["private"]
    assert api["networks"] == ["edge", "private", "mail"]
    assert web["networks"] == ["edge"]
    assert mail["networks"] == ["mail"]
    assert all("ports" not in item for item in (db, api))
    assert web["ports"] == ["127.0.0.1:${E2E_FRONTEND_PORT:?Approved frontend port required}:8080"]
    assert mail["ports"] == ["127.0.0.1:${E2E_MAIL_UI_PORT:?Approved Mailpit UI port required}:8025"]
    assert mail["tmpfs"] == ["/data", "/tmp"] and "volumes" not in mail
    assert "@sha256:" in mail["image"] and ":latest" not in mail["image"]
    assert all(item["restart"] == "no" for item in (db, api, web, mail))


def test_e2e_compose_origin_and_migration_boundary():
    api = COMPOSE["services"]["backend"]
    env = api["environment"]
    assert env["APP_ENV"] == "development" and env["APP_DATABASE_MODE"] == "stack"
    assert env["STACK_RUN_ID"] == env["E2E_RUN_ID"]
    assert env["STACK_DATABASE_HOST"] == "db"
    assert env["STACK_DATABASE_PORT"] == "5432"
    assert env["STACK_DATABASE_USER"] == "jobtracker_stack"
    assert env["AUTH_ALLOWED_ORIGINS"] == env["VERIFICATION_FRONTEND_ORIGIN"]
    assert env["AUTH_ALLOWED_ORIGINS"] == "http://localhost:${E2E_FRONTEND_PORT:?Approved frontend port required}"
    assert env["EMAIL_DELIVERY_MODE"] == "stack-capture"
    assert env["SESSION_COOKIE_SECURE"] == "false"
    assert env["DEV_EMAIL_VERIFICATION_PREVIEW"] == "false"
    assert "MIGRATION_DATABASE_NAME" not in env
    assert "command" not in api and "entrypoint" not in api
    assert "alembic upgrade" not in (ROOT / "backend/Dockerfile").read_text()


def test_e2e_settings_and_stack_guards():
    validate_settings(settings_values(), RUN)
    target = stack_database_target(database_values())
    assert target.name == f"jobtracker_stack_{RUN}"
    assert target.project == f"jobtracker-e2e-{RUN}"
    auth = AuthSettings.from_environment(auth_values())
    assert auth.allowed_browser_origins == (ORIGIN,)
    assert auth.verification_frontend_origin == ORIGIN
    assert StackCaptureEmailSender(auth)._origin == ORIGIN


@pytest.mark.parametrize("change", [
    {"E2E_RUN_ID": "06479b7b1e0fc5d4"},
    {"E2E_RUN_ID": "d3a273dcf94789a9"},
    {"E2E_FRONTEND_PORT": "18080"}, {"E2E_FRONTEND_PORT": "00080"},
    {"E2E_FRONTEND_PORT": "60999", "E2E_MAIL_UI_PORT": "60999"},
    {"E2E_MAIL_UI_PORT": "65536"}, {"E2E_CSRF_HMAC_SECRET": "short"},
])
def test_private_settings_reject_bad_identity_ports_and_secrets(change):
    with pytest.raises(ValueError):
        validate_settings(settings_values() | change, RUN)


@pytest.mark.parametrize("change", [
    {"E2E_RUN_ID": "ffffffffffffffff"},
    {"E2E_FRONTEND_PORT": "18080"},
    {"E2E_FRONTEND_PORT": "22080", "VERIFICATION_FRONTEND_ORIGIN": "http://localhost:22081"},
    {"APP_ENV": "production"}, {"DEV_EMAIL_VERIFICATION_PREVIEW": "true"},
    {"SMTP_HOST": "capture"},
])
def test_auth_rejects_e2e_mismatch_or_unsafe_override(change):
    with pytest.raises(AuthConfigurationError):
        AuthSettings.from_environment(auth_values() | change)


def test_database_target_rejects_e2e_identity_mismatch():
    with pytest.raises(DatabaseConfigurationError):
        stack_database_target(database_values() | {"E2E_RUN_ID": "ffffffffffffffff"})
    for reserved in ("06479b7b1e0fc5d4", "d3a273dcf94789a9"):
        with pytest.raises(DatabaseConfigurationError):
            stack_database_target(database_values() | {
                "STACK_RUN_ID": reserved, "E2E_RUN_ID": reserved,
                "STACK_DATABASE_NAME": f"jobtracker_stack_{reserved}",
            })


def test_runbook_requires_explicit_migration_target_and_separate_approval():
    guide = (ROOT / "docs/playwright-e2e-infrastructure.md").read_text()
    for phrase in ("MIGRATION_DATABASE_NAME", "0005_user_profile", "separate approval",
                   "jobtracker-e2e-", "PLAYWRIGHT_BASE_URL", "source only"):
        assert phrase in guide
    assert "Compose\nuses shell variables before `--env-file` values" in guide
    for name in ("E2E_FRONTEND_PORT", "E2E_MAIL_UI_PORT", "E2E_POSTGRES_PASSWORD",
                 "E2E_CSRF_HMAC_SECRET"):
        assert name in guide.split("unset in the shell:", 1)[0]
    commands = [line for line in guide.splitlines() if line.startswith("docker --context default compose ")]
    assert commands
    prefix = ('docker --context default compose --env-file "$E2E_ENV_FILE" '
              '-f compose.e2e.yaml -p "jobtracker-e2e-${E2E_RUN_ID}" ')
    assert all(line.startswith(prefix) for line in commands)
    assert any(line.endswith("config --quiet") for line in commands)
    assert any("backend python -m alembic upgrade 0005_user_profile" in line for line in commands)
    assert any("backend python -m alembic current" in line for line in commands)
    for line in commands:
        if "python -m alembic" in line or "backend python -c" in line:
            assert '-e "MIGRATION_DATABASE_NAME=jobtracker_stack_${E2E_RUN_ID}"' in line
    probe = guide.split("backend python -c '\n", 1)[1].split("\n'", 1)[0]
    ast.parse(probe)
    for required in ("check_stack_target(connection, target)",
                     'schema.get_table_names(schema="public")',
                     'schema.get_view_names(schema="public")'):
        assert required in probe
    assert not re.search(r"docker(?:\s+--context\s+\S+)?\s+compose\b", guide.replace(
        "docker --context default compose", ""))
