"""Offline safety invariants for the proposed, unprovisioned Compose stack."""
import ast
from pathlib import Path
import re
import textwrap

import yaml


ROOT = Path(__file__).resolve().parents[2]
SOURCE = (ROOT / "compose.stack.yaml").read_text()
STACK = yaml.safe_load(SOURCE)


def test_stack_identity_and_resource_isolation():
    assert STACK["name"] == "jobtracker-stack-${STACK_RUN_ID:?STACK_RUN_ID must be approved}"
    assert set(STACK["services"]) == {"db", "backend", "frontend", "capture"}
    assert set(STACK["networks"]) == {"edge", "private", "mail"}
    assert STACK["networks"]["private"]["internal"] is True
    assert set(STACK["volumes"]) == {"stack_pgdata"}
    assert all(not values or not values.get("external") and not values.get("name")
               for values in STACK["volumes"].values())
    assert all("container_name" not in service for service in STACK["services"].values())
    assert not any(name in SOURCE for name in (
        "jobtracker_db", "jobtracker_demo", "postgres_data", "host.docker.internal",
        "55434", "5432:5432",
    ))


def test_only_frontend_is_published_and_database_is_private():
    db, backend, frontend = (STACK["services"][name] for name in ("db", "backend", "frontend"))
    assert db["image"] == "postgres:17"
    assert db["networks"] == ["private"]
    assert db["volumes"] == ["stack_pgdata:/var/lib/postgresql/data"]
    assert "ports" not in db and "ports" not in backend
    assert backend["networks"] == ["edge", "private", "mail"]
    assert frontend["networks"] == ["edge"]
    assert frontend["ports"] == ["127.0.0.1:18080:8080"]
    assert all(service["restart"] == "no" for service in (db, backend, frontend))


def test_database_identity_secrets_and_migrations_are_explicit():
    db, backend = (STACK["services"][name] for name in ("db", "backend"))
    db_env, api_env = db["environment"], backend["environment"]
    expected_name = "jobtracker_stack_${STACK_RUN_ID:?STACK_RUN_ID must be approved}"
    assert db_env == {
        "POSTGRES_USER": "jobtracker_stack",
        "POSTGRES_DB": expected_name,
        "POSTGRES_PASSWORD": "${STACK_POSTGRES_PASSWORD:?Private stack password is required}",
    }
    assert api_env["APP_ENV"] == "development"
    assert api_env["APP_DATABASE_MODE"] == "stack"
    assert api_env["STACK_RUN_ID"] == "${STACK_RUN_ID:?STACK_RUN_ID must be approved}"
    assert api_env["STACK_DATABASE_NAME"] == expected_name
    assert (api_env["STACK_DATABASE_HOST"], api_env["STACK_DATABASE_PORT"],
            api_env["STACK_DATABASE_USER"]) == ("db", "5432", "jobtracker_stack")
    assert api_env["STACK_POSTGRES_PASSWORD"] == db_env["POSTGRES_PASSWORD"]
    assert api_env["CSRF_HMAC_SECRET"] == "${STACK_CSRF_HMAC_SECRET:?Private stack CSRF secret is required}"
    assert api_env["AUTH_ALLOWED_ORIGINS"] == api_env["VERIFICATION_FRONTEND_ORIGIN"] == "http://localhost:18080"
    assert api_env["SESSION_COOKIE_SECURE"] == "false"
    assert api_env["EMAIL_DELIVERY_MODE"] == "${STACK_EMAIL_DELIVERY_MODE:-none}"
    assert api_env["DEV_EMAIL_VERIFICATION_PREVIEW"] == "false"
    assert "MIGRATION_DATABASE_NAME" not in api_env
    assert "command" not in backend and "entrypoint" not in backend


def test_mail_capture_is_disposable_opt_in_and_separate_from_database():
    capture = STACK["services"]["capture"]
    assert capture["profiles"] == ["mail-capture"]
    assert capture["image"] == "${STACK_MAILPIT_IMAGE:-invalid.local/mailpit-digest-required}"
    assert capture["networks"] == ["mail"]
    assert "mail" not in STACK["services"]["db"]["networks"]
    assert "mail" not in STACK["services"]["frontend"]["networks"]
    assert STACK["networks"]["mail"]["driver"] == "bridge"
    assert STACK["networks"]["mail"].get("internal") is not True
    assert capture["ports"] == ["127.0.0.1:18025:8025"]
    assert capture["environment"] == {"MP_DATABASE": "/data/mailpit.db"}
    assert capture["tmpfs"] == ["/data", "/tmp"]
    assert capture["read_only"] is True
    assert capture["restart"] == "no"
    assert capture["cap_drop"] == ["ALL"]
    assert "volumes" not in capture and "depends_on" not in capture
    assert "1025:" not in SOURCE


def test_health_dependencies_and_runbook_safety():
    db, backend, frontend = (STACK["services"][name] for name in ("db", "backend", "frontend"))
    assert db["healthcheck"]["test"][0] == "CMD-SHELL"
    assert backend["depends_on"]["db"]["condition"] == "service_healthy"
    assert frontend["depends_on"]["backend"]["condition"] == "service_healthy"
    assert "/health" in backend["healthcheck"]["test"][-1]
    assert "/health" in frontend["healthcheck"]["test"][-1]
    guide = (ROOT / "docs/container-stack.md").read_text()
    for required in ("--env-file", "config --quiet", "0005_user_profile", "/health/db",
                     "stop frontend backend db", "EMAIL_DELIVERY_MODE=none", "separate approval"):
        assert required in guide
    commands = [line.strip() for line in guide.splitlines()
                if re.match(r"docker(?:\s+--context\s+\S+)?\s+compose\b", line.strip())]
    assert commands
    approved_prefix = ('docker --context default compose --env-file "$STACK_ENV_FILE" '
                       '-f compose.stack.yaml -p "jobtracker-stack-${STACK_RUN_ID}" ')
    assert all(line.startswith(approved_prefix) for line in commands)
    assert any(line.endswith("config --quiet") for line in commands)
    assert any(line.endswith("up -d --no-deps db") for line in commands)
    assert any(line.endswith("stop frontend backend db") for line in commands)
    assert "unset STACK_RUN_ID COMPOSE_PROJECT_NAME COMPOSE_FILE" in guide
    assert "COMPOSE_PROFILES" in guide
    assert 'network inspect "jobtracker-stack-${STACK_RUN_ID}_mail"' in guide
    assert "For a **first deployment**, the project-scoped `mail` network must be absent" in guide
    assert "the backend's network declaration may already have created it" in guide
    assert "path != expected" in guide and "settings['STACK_RUN_ID'] != run_id" in guide
    preflight = guide.split("backend python -c '", 1)[1].split("\n   '", 1)[0]
    ast.parse(textwrap.dedent(preflight))
    assert "if identity != expected or tables:" in preflight
    migration_target = '-e "MIGRATION_DATABASE_NAME=jobtracker_stack_${STACK_RUN_ID}"'
    for operation in ("upgrade 0005_user_profile", "current", "check"):
        assert re.search(r"run --no-deps \\\s*" + re.escape(migration_target)
                         + r" \\\s*backend python -m alembic " + re.escape(operation), guide)
    dockerfile = (ROOT / "backend/Dockerfile").read_text()
    assert '"app.main:app"' in dockerfile and "alembic upgrade" not in dockerfile
    assert "MIGRATION_DATABASE_NAME" not in STACK["services"]["backend"]["environment"]


def test_nginx_and_vite_agree_on_application_page_and_api_paths():
    nginx = (ROOT / "frontend/nginx.conf").read_text()
    vite = (ROOT / "frontend/vite.config.js").read_text()
    routes = (ROOT / "frontend/src/App.jsx").read_text()
    for path in ("/applications", "/applications/"):
        block = nginx.split(f"location = {path} {{", 1)[1].split("\n        }", 1)[0]
        assert "if ($applications_spa)" in block
        assert "rewrite ^ /index.html last;" in block
        assert "proxy_pass http://backend:8000;" in block
    assert "~*^GET:.*text/html 1;" in nginx
    assert "location ~ ^/(?:auth(?:/|$)|applications/)" in nginx
    assert "proxy_intercept_errors off;" in nginx
    assert "location / {\n            try_files $uri $uri/ /index.html;" in nginx
    assert "'/auth':" in vite and "'/applications':" in vite
    assert "request.method === 'GET'" in vite and "text/html" in vite
    for route in ('login', 'register', 'verify-email', 'applications', 'analytics', 'profile'):
        assert f'path="{route}"' in routes or f'path="/{route}"' in routes
