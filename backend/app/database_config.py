"""Pure database configuration guards; no environment loading or connections."""
import re
from dataclasses import dataclass, field

FORBIDDEN_DATABASE_NAMES = frozenset({"jobtracker_db", "postgres", "template0", "template1"})
DEMO_HOST = "127.0.0.1"
DEMO_PORT = 55434
DEMO_USER = "jobtracker_demo"
STACK_HOST = "db"
STACK_PORT = 5432
STACK_USER = "jobtracker_stack"
STACK_KEYS = frozenset({
    "STACK_RUN_ID", "STACK_DATABASE_NAME", "STACK_DATABASE_HOST",
    "STACK_DATABASE_PORT", "STACK_DATABASE_USER", "STACK_POSTGRES_PASSWORD",
})


class DatabaseConfigurationError(ValueError):
    """Errors name settings, never supplied values or credentials."""


@dataclass(frozen=True)
class DemoDatabaseTarget:
    name: str
    project: str
    password: str = field(repr=False)


@dataclass(frozen=True)
class StackDatabaseTarget:
    name: str
    project: str
    password: str = field(repr=False)
    host: str = STACK_HOST
    port: int = STACK_PORT
    user: str = STACK_USER


def stack_database_target(values):
    """One explicit development Compose target; never infer or accept a URL."""
    if runtime_database_mode(values) != "stack":
        raise DatabaseConfigurationError("APP_DATABASE_MODE=stack is required")
    if values.get("APP_ENV") != "development":
        raise DatabaseConfigurationError("APP_ENV=development must be explicit in stack mode")
    run_id = values.get("STACK_RUN_ID")
    if not isinstance(run_id, str) or not re.fullmatch(r"[0-9a-f]{16}", run_id):
        raise DatabaseConfigurationError("STACK_RUN_ID must be 16 lowercase hex characters")
    name = validate_database_name(f"jobtracker_stack_{run_id}", "Stack database name")
    expected = {
        "STACK_DATABASE_NAME": name, "STACK_DATABASE_HOST": STACK_HOST,
        "STACK_DATABASE_PORT": str(STACK_PORT), "STACK_DATABASE_USER": STACK_USER,
    }
    if any(values.get(key) != value for key, value in expected.items()):
        raise DatabaseConfigurationError("Stack database identity settings must match the selected run")
    password = values.get("STACK_POSTGRES_PASSWORD")
    if (not isinstance(password, str) or not 16 <= len(password) <= 1024
            or password.isspace() or any(ord(char) < 32 or ord(char) == 127 for char in password)):
        raise DatabaseConfigurationError("STACK_POSTGRES_PASSWORD is required and must be a strong private value")
    conflicts = (
        "APP_DATABASE_NAME", "APP_DATABASE_HOST", "APP_DATABASE_PORT", "APP_DATABASE_USER",
        "POSTGRES_PASSWORD", "DEMO_RUN_ID", "DEMO_POSTGRES_PASSWORD",
        "DEMO_DATABASE_NAME", "DEMO_DATABASE_HOST", "DEMO_DATABASE_PORT", "DEMO_DATABASE_USER",
        "MIGRATION_DATABASE_HOST", "MIGRATION_DATABASE_PORT", "MIGRATION_DATABASE_USER",
        "DATABASE_URL", "PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD",
    )
    if any(key in values for key in conflicts):
        raise DatabaseConfigurationError("Stack mode conflicts with other database target settings")
    return StackDatabaseTarget(name, f"jobtracker-stack-{run_id}", password)


def demo_database_target(values):
    """Validate one explicitly selected development run without connecting."""
    if runtime_database_mode(values) != "demo":
        raise DatabaseConfigurationError("APP_DATABASE_MODE=demo is required")
    if values.get("APP_ENV") != "development":
        raise DatabaseConfigurationError("APP_ENV=development must be explicit in demo mode")
    run_id = values.get("DEMO_RUN_ID")
    if not isinstance(run_id, str) or not re.fullmatch(r"[0-9a-f]{16}", run_id):
        raise DatabaseConfigurationError("DEMO_RUN_ID must be 16 lowercase hex characters")
    password = values.get("DEMO_POSTGRES_PASSWORD")
    if (not isinstance(password, str) or not 16 <= len(password) <= 1024
            or password.isspace() or any(ord(char) < 32 or ord(char) == 127 for char in password)):
        raise DatabaseConfigurationError("DEMO_POSTGRES_PASSWORD is required and must be a strong private value")
    if password == values.get("POSTGRES_PASSWORD"):
        raise DatabaseConfigurationError("DEMO_POSTGRES_PASSWORD must differ from ordinary credentials")
    # Never interpret ordinary or custom-host settings as demo overrides.
    conflicts = ("APP_DATABASE_NAME", "APP_DATABASE_HOST", "APP_DATABASE_PORT", "APP_DATABASE_USER",
                 "MIGRATION_DATABASE_HOST", "MIGRATION_DATABASE_PORT", "MIGRATION_DATABASE_USER",
                 "DEMO_DATABASE_NAME", "DEMO_DATABASE_HOST", "DEMO_DATABASE_PORT", "DEMO_DATABASE_USER")
    if any(key in values for key in conflicts):
        raise DatabaseConfigurationError("Demo database configuration conflicts with other target settings")
    name = validate_database_name(f"jobtracker_demo_{run_id}", "Demo database name")
    return DemoDatabaseTarget(name, f"jobtracker-demo-{run_id}", password)


def validate_database_name(name, setting):
    if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", name):
        raise DatabaseConfigurationError(
            f"{setting} is required and must contain 1-63 lowercase ASCII "
            "letters, digits or underscores, starting with a letter"
        )
    if name in FORBIDDEN_DATABASE_NAMES:
        raise DatabaseConfigurationError(f"{setting} selects a forbidden database")
    return name


def runtime_database_mode(values):
    """Missing mode preserves ordinary behavior; explicit invalid modes fail closed."""
    mode = values.get("APP_DATABASE_MODE", "ordinary")
    if mode not in ("ordinary", "disposable", "demo", "stack"):
        raise DatabaseConfigurationError("APP_DATABASE_MODE must be ordinary, disposable, demo or stack")
    if mode != "stack" and any(key in values for key in STACK_KEYS):
        raise DatabaseConfigurationError("Stack database settings require APP_DATABASE_MODE=stack")
    environment = values.get("APP_ENV", "development")
    if not isinstance(environment, str) or environment.strip().lower() not in ("development", "production"):
        raise DatabaseConfigurationError("APP_ENV must be development or production")
    if mode in ("disposable", "demo", "stack") and environment.strip().lower() == "production":
        raise DatabaseConfigurationError(f"APP_DATABASE_MODE {mode} is forbidden in production")
    return mode


def runtime_database_name(values):
    mode = runtime_database_mode(values)
    if mode == "disposable":
        if any(key in values for key in ("APP_DATABASE_NAME", "POSTGRES_PASSWORD")):
            raise DatabaseConfigurationError("Disposable mode conflicts with ordinary database credentials or target")
        raise DatabaseConfigurationError("Disposable mode is unavailable until receipt and lease validation is implemented")
    if mode == "demo":
        return demo_database_target(values).name
    if mode == "stack":
        return stack_database_target(values).name
    # These are not configurable in ordinary mode. Reject misleading overrides.
    for key, expected in (("APP_DATABASE_HOST", "127.0.0.1"),
                          ("APP_DATABASE_PORT", "5432"), ("APP_DATABASE_USER", "jobtracker")):
        if key in values and values[key] != expected:
            raise DatabaseConfigurationError(f"{key} conflicts with the ordinary runtime target")
    return validate_database_name(values.get("APP_DATABASE_NAME"), "APP_DATABASE_NAME")
