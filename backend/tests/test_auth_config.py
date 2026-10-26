import pytest

from app.auth_config import AuthConfigurationError, AuthSettings


def production_values():
    return {
        "APP_ENV": "production",
        "CSRF_HMAC_SECRET": "test-only-key-not-for-deployment-1234567890",
        "AUTH_ALLOWED_ORIGINS": "https://tracker.example.com",
    }


def test_development_defaults():
    settings = AuthSettings.from_environment({})
    assert settings.environment == "development"
    assert settings.session_cookie_name == "session"
    assert settings.session_cookie_secure is False
    assert settings.session_absolute_timeout_seconds == 28800
    assert settings.session_idle_timeout_seconds == 1800
    assert settings.password_hash_max_concurrency == 2
    assert settings.json_max_body_bytes == 65536
    assert len(settings.csrf_hmac_secret) == 32
    assert settings.allowed_browser_origins == ("http://localhost:5173", "http://127.0.0.1:5173")
    assert settings.csrf_hmac_secret != AuthSettings.from_environment({}).csrf_hmac_secret
    assert "csrf_hmac_secret" not in repr(settings)


def test_valid_production_defaults():
    settings = AuthSettings.from_environment(production_values())
    assert settings.session_cookie_secure is True
    assert settings.session_cookie_name == "__Host-session"
    assert settings.allowed_browser_origins == ("https://tracker.example.com",)
    assert production_values()["CSRF_HMAC_SECRET"] not in repr(settings)


def test_explicit_settings_and_origin_normalization():
    settings = AuthSettings.from_environment({
        "SESSION_COOKIE_NAME": "custom-session",
        "SESSION_COOKIE_SECURE": "true",
        "SESSION_ABSOLUTE_TIMEOUT_SECONDS": "3600",
        "SESSION_IDLE_TIMEOUT_SECONDS": "600",
        "CSRF_HMAC_SECRET": "test-only-development-secret-123456789",
        "AUTH_ALLOWED_ORIGINS": "https://EXAMPLE.com:443, https://example.com,http://[::1]:5173",
    })
    assert settings.session_cookie_name == "custom-session"
    assert settings.session_cookie_secure is True
    assert settings.session_absolute_timeout_seconds == 3600
    assert settings.session_idle_timeout_seconds == 600
    assert settings.allowed_browser_origins == ("https://example.com", "http://[::1]:5173")


@pytest.mark.parametrize("missing", ["CSRF_HMAC_SECRET", "AUTH_ALLOWED_ORIGINS"])
def test_production_requires_explicit_secret_and_origins(missing):
    values = production_values()
    del values[missing]
    with pytest.raises(AuthConfigurationError, match=missing):
        AuthSettings.from_environment(values)


@pytest.mark.parametrize("overrides", [
    {"APP_ENV": "prod"}, {"APP_ENV": ""}, {"SESSION_COOKIE_SECURE": "yes"},
    {"SESSION_COOKIE_NAME": "bad;cookie"}, {"SESSION_COOKIE_NAME": ""},
    {"SESSION_COOKIE_NAME": "__Host-session", "SESSION_COOKIE_SECURE": "false"},
    {"SESSION_COOKIE_NAME": "__Secure-session", "SESSION_COOKIE_SECURE": "false"},
    {"SESSION_ABSOLUTE_TIMEOUT_SECONDS": "0"}, {"SESSION_ABSOLUTE_TIMEOUT_SECONDS": "604801"},
    {"SESSION_ABSOLUTE_TIMEOUT_SECONDS": "3.5"}, {"SESSION_IDLE_TIMEOUT_SECONDS": "-1"},
    {"SESSION_IDLE_TIMEOUT_SECONDS": "86401"},
    {"SESSION_ABSOLUTE_TIMEOUT_SECONDS": "10", "SESSION_IDLE_TIMEOUT_SECONDS": "11"},
    {"CSRF_HMAC_SECRET": ""}, {"CSRF_HMAC_SECRET": "short-private-secret"},
    {"CSRF_HMAC_SECRET": " " * 32}, {"CSRF_HMAC_SECRET": "x" * 1025},
    {"CSRF_HMAC_SECRET": "\ud800" * 32},
])
def test_invalid_configuration_is_rejected(overrides):
    with pytest.raises(AuthConfigurationError):
        AuthSettings.from_environment(overrides)


@pytest.mark.parametrize("origin", [
    "", "*", "https://*.example.com", "null", "example.com", "ftp://example.com",
    "https://example.com/", "https://example.com/path", "https://example.com?q=secret",
    "https://example.com/#secret", "https://user:private@example.com",
    "https://example.com:invalid", "https://example.com:70000", "https://example.com:0",
    "https://example.com:", "https://bad host.com", "https://example.com,",
    "https://example.com?", "https://example.com#", "\x00https://example.com",
])
def test_invalid_origins_are_rejected(origin):
    with pytest.raises(AuthConfigurationError, match="AUTH_ALLOWED_ORIGINS"):
        AuthSettings.from_environment({"AUTH_ALLOWED_ORIGINS": origin})


@pytest.mark.parametrize("overrides", [
    {"SESSION_COOKIE_SECURE": "false"}, {"SESSION_COOKIE_NAME": "session"},
    {"AUTH_ALLOWED_ORIGINS": "http://tracker.example.com"}, {"CSRF_HMAC_SECRET": "short-secret"},
])
def test_production_rejects_insecure_configuration(overrides):
    with pytest.raises(AuthConfigurationError):
        AuthSettings.from_environment(production_values() | overrides)


def test_configuration_errors_and_logs_do_not_reveal_values(caplog):
    for name, value in [
        ("CSRF_HMAC_SECRET", "private-secret-too-short"),
        ("AUTH_ALLOWED_ORIGINS", "https://private:credential@example.com"),
        ("SESSION_IDLE_TIMEOUT_SECONDS", "private-invalid-timeout"),
    ]:
        with pytest.raises(AuthConfigurationError) as error:
            AuthSettings.from_environment({name: value})
        assert value not in str(error.value)
        assert value not in repr(error.value)
        assert value not in caplog.text


def test_cached_settings_respect_exported_environment(monkeypatch):
    import app.auth_config as config

    variables = (
        "APP_ENV", "SESSION_COOKIE_NAME", "SESSION_COOKIE_SECURE",
        "SESSION_ABSOLUTE_TIMEOUT_SECONDS", "SESSION_IDLE_TIMEOUT_SECONDS",
        "CSRF_HMAC_SECRET", "AUTH_ALLOWED_ORIGINS",
        "PASSWORD_HASH_MAX_CONCURRENCY", "JSON_MAX_BODY_BYTES",
    )
    for variable in variables:
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("SESSION_COOKIE_NAME", "exported-session")
    calls = []
    monkeypatch.setattr(config, "load_dotenv", lambda path, override: calls.append(override))
    config.get_auth_settings.cache_clear()
    try:
        first = config.get_auth_settings()
        assert first.session_cookie_name == "exported-session"
        assert first is config.get_auth_settings()
        assert calls == [False]
    finally:
        config.get_auth_settings.cache_clear()



def test_application_import_rejects_insecure_production():
    import os
    import subprocess
    import sys

    environment = os.environ.copy()
    environment.update(APP_ENV="production", SESSION_COOKIE_SECURE="false",
                       POSTGRES_PASSWORD="test-only-not-used")
    result = subprocess.run(
        [sys.executable, "-c", "import app.main"], env=environment,
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode != 0
    assert "SESSION_COOKIE_SECURE must be true in production" in result.stderr
    assert "test-only-not-used" not in result.stderr


@pytest.mark.parametrize("values, expected", [
    ({"PASSWORD_HASH_MAX_CONCURRENCY": "1", "JSON_MAX_BODY_BYTES": "1"}, (1, 1)),
    ({"PASSWORD_HASH_MAX_CONCURRENCY": "8", "JSON_MAX_BODY_BYTES": "1048576"}, (8, 1048576)),
])
def test_resource_limit_configuration(values, expected):
    settings = AuthSettings.from_environment(values)
    assert (settings.password_hash_max_concurrency, settings.json_max_body_bytes) == expected


@pytest.mark.parametrize("name, value", [
    ("PASSWORD_HASH_MAX_CONCURRENCY", "0"), ("PASSWORD_HASH_MAX_CONCURRENCY", "9"),
    ("JSON_MAX_BODY_BYTES", "0"), ("JSON_MAX_BODY_BYTES", "1048577"),
    ("PASSWORD_HASH_MAX_CONCURRENCY", "private-invalid-marker"),
    ("JSON_MAX_BODY_BYTES", "private-invalid-marker"),
    ("JSON_MAX_BODY_BYTES", "-1"), ("PASSWORD_HASH_MAX_CONCURRENCY", "1.5"),
])
def test_invalid_resource_limits_are_sanitized(name, value, caplog):
    with pytest.raises(AuthConfigurationError) as error:
        AuthSettings.from_environment({name: value})
    assert name in str(error.value)
    assert "private-invalid-marker" not in str(error.value) + caplog.text

def test_email_verification_preview_disabled_by_default():
    settings = AuthSettings.from_environment({})
    assert settings.dev_email_verification_preview is False


def test_email_verification_preview_enabled_in_development():
    settings = AuthSettings.from_environment({
        "APP_ENV": "development",
        "DEV_EMAIL_VERIFICATION_PREVIEW": "true",
        "VERIFICATION_FRONTEND_ORIGIN": "http://localhost:5173",
    })
    assert settings.dev_email_verification_preview is True
    assert settings.verification_frontend_origin == 'http://localhost:5173'


def test_preview_requires_explicit_frontend_origin():
    with pytest.raises(AuthConfigurationError, match='VERIFICATION_FRONTEND_ORIGIN'):
        AuthSettings.from_environment({'DEV_EMAIL_VERIFICATION_PREVIEW': 'true'})


@pytest.mark.parametrize('origin', [
    'http://evil.example', 'http://localhost:5173/path',
    'http://localhost:5173/#token=private-token',
    'https://person:private@example.com',
])
def test_verification_frontend_origin_must_be_exact_and_allowed(origin, caplog):
    with pytest.raises(AuthConfigurationError) as error:
        AuthSettings.from_environment({'VERIFICATION_FRONTEND_ORIGIN': origin})
    assert origin not in str(error.value) + caplog.text


def test_production_verification_origin_requires_https_and_allowlist():
    values = production_values() | {'VERIFICATION_FRONTEND_ORIGIN': 'https://tracker.example.com'}
    assert AuthSettings.from_environment(values).verification_frontend_origin == 'https://tracker.example.com'
    with pytest.raises(AuthConfigurationError):
        AuthSettings.from_environment(values | {'VERIFICATION_FRONTEND_ORIGIN': 'http://tracker.example.com'})


def test_email_verification_preview_rejected_in_production():
    with pytest.raises(
        AuthConfigurationError,
        match="DEV_EMAIL_VERIFICATION_PREVIEW",
    ):
        AuthSettings.from_environment(
            production_values() | {
                "DEV_EMAIL_VERIFICATION_PREVIEW": "true",
            }
        )

@pytest.mark.parametrize("value", ["yes", "1", "", "invalid"])
def test_invalid_email_verification_preview_setting(value):
    with pytest.raises(
        AuthConfigurationError,
        match="DEV_EMAIL_VERIFICATION_PREVIEW",
    ):
        AuthSettings.from_environment({
            "DEV_EMAIL_VERIFICATION_PREVIEW": value,
        })


def test_demo_refuses_real_smtp_even_with_complete_smtp_settings():
    demo_password = "synthetic-demo-only-private-password"
    values = {
        "APP_ENV": "development",
        "APP_DATABASE_MODE": "demo",
        "DEMO_RUN_ID": "a1b2c3d4e5f60718",
        "DEMO_POSTGRES_PASSWORD": demo_password,
        "EMAIL_DELIVERY_MODE": "smtp",
        "VERIFICATION_FRONTEND_ORIGIN": "http://localhost:5173",
        "SMTP_HOST": "smtp.example.test", "SMTP_PORT": "587",
        "SMTP_USERNAME": "demo", "SMTP_PASSWORD": demo_password,
        "SMTP_FROM_EMAIL": "verify@example.test",
    }
    with pytest.raises(AuthConfigurationError, match="forbidden in demo mode") as error:
        AuthSettings.from_environment(values)
    assert demo_password not in str(error.value)
