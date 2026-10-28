"""Offline SMTP adapter tests. No real SMTP or database connections."""
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import os
import subprocess
import sys
from unittest.mock import MagicMock, Mock

import pytest

from app.auth_config import AuthConfigurationError, AuthSettings
from app.email_delivery import (
    EmailDeliveryError, LocalCaptureEmailSender, StackCaptureEmailSender,
    SmtpEmailSender, build_email_sender,
)


SECRET = "synthetic-private-smtp-password"
TOKEN = "a" * 43
ORIGIN = "https://tracker.example.com"
LINK = ORIGIN + "/verify-email#token=" + TOKEN


def smtp_values():
    return {
        "EMAIL_DELIVERY_MODE": "smtp",
        "VERIFICATION_FRONTEND_ORIGIN": ORIGIN,
        "AUTH_ALLOWED_ORIGINS": ORIGIN,
        "SMTP_HOST": "smtp.example.test",
        "SMTP_PORT": "587",
        "SMTP_USERNAME": "smtp-user",
        "SMTP_PASSWORD": SECRET,
        "SMTP_FROM_EMAIL": "verify@example.com",
        "SMTP_TIMEOUT_SECONDS": "5",
    }


def sender():
    return build_email_sender(AuthSettings.from_environment(smtp_values()))


def capture_values():
    return {
        "APP_ENV": "development",
        "EMAIL_DELIVERY_MODE": "local-capture",
        "VERIFICATION_FRONTEND_ORIGIN": "http://localhost:5173",
    }


def stack_capture_values():
    return {
        "APP_ENV": "development", "APP_DATABASE_MODE": "stack",
        "EMAIL_DELIVERY_MODE": "stack-capture",
        "VERIFICATION_FRONTEND_ORIGIN": "http://localhost:18080",
        "AUTH_ALLOWED_ORIGINS": "http://localhost:18080",
    }


def test_stack_capture_uses_only_internal_mailpit_and_fragment_link(monkeypatch):
    client, calls = fake_smtp()
    connector = Mock(return_value=client)
    monkeypatch.setattr("app.email_delivery.smtplib.SMTP", connector)
    settings = AuthSettings.from_environment(stack_capture_values())
    capture = build_email_sender(settings)
    assert isinstance(capture, StackCaptureEmailSender)
    link = "http://localhost:18080/verify-email#token=" + TOKEN
    capture.send_verification("person@example.com", link, datetime.now(UTC))
    connector.assert_called_once_with("capture", 1025, timeout=5)
    assert [call if isinstance(call, str) else call[0] for call in calls] == ["ehlo", "send"]
    assert link in calls[-1][1].get_content()
    assert TOKEN not in repr(capture) + repr(settings)


@pytest.mark.parametrize("change", [
    {"APP_ENV": "production"}, {"APP_DATABASE_MODE": "demo"},
    {"APP_DATABASE_MODE": "ordinary"}, {"VERIFICATION_FRONTEND_ORIGIN": "http://localhost:5173"},
    {"VERIFICATION_FRONTEND_ORIGIN": "http://127.0.0.1:18080"},
    {"AUTH_ALLOWED_ORIGINS": "http://localhost:18080,http://localhost:5173"},
    {"SMTP_HOST": "capture"}, {"SMTP_PASSWORD": SECRET},
    {"LOCAL_CAPTURE_HOST": "capture"}, {"DEV_EMAIL_VERIFICATION_PREVIEW": "true"},
])
def test_stack_capture_rejects_unsafe_configuration(change, caplog):
    with pytest.raises(AuthConfigurationError) as captured:
        AuthSettings.from_environment(stack_capture_values() | change)
    assert SECRET not in str(captured.value) + repr(captured.value) + caplog.text


def test_stack_capture_revalidates_settings_and_redacts_transport_failure(monkeypatch, caplog):
    settings = AuthSettings.from_environment(stack_capture_values())
    with pytest.raises(AuthConfigurationError):
        build_email_sender(replace(settings, environment="production"))
    connector = Mock(side_effect=OSError(SECRET + TOKEN))
    monkeypatch.setattr("app.email_delivery.smtplib.SMTP", connector)
    with pytest.raises(EmailDeliveryError) as captured:
        build_email_sender(settings).send_verification(
            "person@example.com", "http://localhost:18080/verify-email#token=" + TOKEN,
            datetime.now(UTC))
    assert connector.call_count == 1
    assert captured.value.__cause__ is None
    assert SECRET not in str(captured.value) + caplog.text
    assert TOKEN not in str(captured.value) + caplog.text


def test_stack_capture_rejects_wrong_link_origin_before_smtp(monkeypatch):
    connector = Mock(side_effect=AssertionError("SMTP connection forbidden"))
    monkeypatch.setattr("app.email_delivery.smtplib.SMTP", connector)
    capture = build_email_sender(AuthSettings.from_environment(stack_capture_values()))
    with pytest.raises(EmailDeliveryError):
        capture.send_verification(
            "person@example.com", "http://localhost:5173/verify-email#token=" + TOKEN,
            datetime.now(UTC))
    connector.assert_not_called()


def test_stack_mode_does_not_allow_real_smtp():
    with pytest.raises(AuthConfigurationError, match="EMAIL_DELIVERY_MODE"):
        AuthSettings.from_environment(smtp_values() | {"APP_DATABASE_MODE": "stack"})


def test_stack_capture_requires_explicit_stack_database_mode():
    values = stack_capture_values()
    values.pop("APP_DATABASE_MODE")
    with pytest.raises(AuthConfigurationError, match="development stack"):
        AuthSettings.from_environment(values)


def fake_smtp(refused=None, failure=None):
    calls = []
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = False
    client.ehlo.side_effect = lambda: calls.append("ehlo")
    client.starttls.side_effect = lambda **kwargs: calls.append(("starttls", kwargs["context"]))
    client.login.side_effect = lambda *args: calls.append(("login", args))
    client.send_message.side_effect = (lambda message: calls.append(("send", message)) or refused)
    if failure:
        getattr(client, failure).side_effect = RuntimeError(SECRET + TOKEN)
    return client, calls


def test_smtp_acceptance_uses_verified_tls_before_auth_and_plain_text_message(monkeypatch):
    client, calls = fake_smtp()
    connector = Mock(return_value=client)
    monkeypatch.setattr("app.email_delivery.smtplib.SMTP", connector)
    expiry = datetime.now(UTC) + timedelta(hours=1)
    sender().send_verification("person@example.com", LINK, expiry)
    connector.assert_called_once_with("smtp.example.test", 587, timeout=5)
    assert [call if isinstance(call, str) else call[0] for call in calls] == [
        "ehlo", "starttls", "ehlo", "login", "send",
    ]
    context = calls[1][1]
    assert context.verify_mode.name == "CERT_REQUIRED" and context.check_hostname
    assert calls[3][1] == ("smtp-user", SECRET)
    message = calls[4][1]
    assert message["From"] == "verify@example.com"
    assert message["To"] == "person@example.com"
    assert message.get_content_type() == "text/plain"
    assert LINK in message.get_content()


def test_local_capture_uses_fixed_loopback_without_tls_or_auth(monkeypatch):
    client, calls = fake_smtp()
    connector = Mock(return_value=client)
    monkeypatch.setattr("app.email_delivery.smtplib.SMTP", connector)
    settings = AuthSettings.from_environment(capture_values())
    capture = build_email_sender(settings)
    assert isinstance(capture, LocalCaptureEmailSender)
    link = "http://localhost:5173/verify-email#token=" + TOKEN
    capture.send_verification("person@example.com", link, datetime.now(UTC))
    connector.assert_called_once_with("127.0.0.1", 11025, timeout=5)
    assert [call if isinstance(call, str) else call[0] for call in calls] == ["ehlo", "send"]
    message = calls[-1][1]
    assert message["From"] == "verify@jobtracker.test"
    assert message["To"] == "person@example.com"
    assert link in message.get_content()
    assert "token=" not in repr(capture) + repr(settings)


@pytest.mark.parametrize("change", [
    {"APP_ENV": "production"},
    {"SMTP_HOST": "127.0.0.1"}, {"SMTP_PORT": "11025"},
    {"SMTP_USERNAME": "user"}, {"SMTP_PASSWORD": SECRET},
    {"LOCAL_CAPTURE_HOST": "smtp.example.test"}, {"LOCAL_CAPTURE_PORT": "25"},
    {"VERIFICATION_FRONTEND_ORIGIN": "https://tracker.example.com"},
    {"VERIFICATION_FRONTEND_ORIGIN": "http://localhost:9999"},
    {"DEV_EMAIL_VERIFICATION_PREVIEW": "true"},
])
def test_local_capture_rejects_production_overrides_and_conflicts(change, caplog):
    with pytest.raises(AuthConfigurationError) as captured:
        AuthSettings.from_environment(capture_values() | change)
    assert SECRET not in str(captured.value) + repr(captured.value) + caplog.text


def test_local_capture_revalidates_manually_constructed_settings():
    settings = AuthSettings.from_environment(capture_values())
    for change in ({"environment": "production"}, {"smtp": AuthSettings.from_environment(smtp_values()).smtp},
                   {"verification_frontend_origin": "https://tracker.example.com"}):
        with pytest.raises(AuthConfigurationError):
            build_email_sender(replace(settings, **change))


def test_local_capture_failure_is_sanitized_and_never_retried(monkeypatch, caplog):
    link = "http://localhost:5173/verify-email#token=" + TOKEN
    connector = Mock(side_effect=OSError(SECRET + TOKEN))
    monkeypatch.setattr("app.email_delivery.smtplib.SMTP", connector)
    with pytest.raises(EmailDeliveryError) as captured:
        build_email_sender(AuthSettings.from_environment(capture_values())).send_verification(
            "person@example.com", link, datetime.now(UTC))
    assert connector.call_count == 1
    assert captured.value.__cause__ is None
    assert SECRET not in str(captured.value) + repr(captured.value) + caplog.text
    assert TOKEN not in str(captured.value) + repr(captured.value) + caplog.text


def test_local_capture_rejects_invalid_link_before_connecting(monkeypatch):
    connector = Mock(side_effect=AssertionError("SMTP connection forbidden"))
    monkeypatch.setattr("app.email_delivery.smtplib.SMTP", connector)
    with pytest.raises(EmailDeliveryError):
        build_email_sender(AuthSettings.from_environment(capture_values())).send_verification(
            "person@example.com", "http://localhost:5173/verify-email?token=" + TOKEN,
            datetime.now(UTC))
    connector.assert_not_called()


@pytest.mark.parametrize("failure", ["ehlo", "starttls", "login", "send_message"])
def test_smtp_transport_failure_is_sanitized(monkeypatch, caplog, failure):
    client, _ = fake_smtp(failure=failure)
    monkeypatch.setattr("app.email_delivery.smtplib.SMTP", Mock(return_value=client))
    with pytest.raises(EmailDeliveryError) as captured:
        sender().send_verification("person@example.com", LINK, datetime.now(UTC))
    assert captured.value.__cause__ is None
    assert SECRET not in str(captured.value) + repr(captured.value) + caplog.text
    assert TOKEN not in str(captured.value) + repr(captured.value) + caplog.text


def test_smtp_connection_failure_and_refusal_are_sanitized(monkeypatch, caplog):
    monkeypatch.setattr("app.email_delivery.smtplib.SMTP",
                        Mock(side_effect=OSError(SECRET + TOKEN)))
    with pytest.raises(EmailDeliveryError) as captured:
        sender().send_verification("person@example.com", LINK, datetime.now(UTC))
    assert SECRET not in str(captured.value) + caplog.text
    assert TOKEN not in str(captured.value) + caplog.text
    client, _ = fake_smtp(refused={"person@example.com": (550, SECRET + TOKEN)})
    monkeypatch.setattr("app.email_delivery.smtplib.SMTP", Mock(return_value=client))
    with pytest.raises(EmailDeliveryError) as captured:
        sender().send_verification("person@example.com", LINK, datetime.now(UTC))
    assert SECRET not in str(captured.value) + caplog.text
    assert TOKEN not in str(captured.value) + caplog.text


def test_smtp_timeout_is_sanitized_and_never_retried(monkeypatch, caplog):
    connector = Mock(side_effect=TimeoutError(SECRET + TOKEN))
    monkeypatch.setattr("app.email_delivery.smtplib.SMTP", connector)
    with pytest.raises(EmailDeliveryError) as captured:
        sender().send_verification("person@example.com", LINK, datetime.now(UTC))
    assert connector.call_count == 1
    assert SECRET not in str(captured.value) + caplog.text
    assert TOKEN not in str(captured.value) + caplog.text


@pytest.mark.parametrize("recipient,link", [
    ("bad\nBcc:evil@example.com", LINK), ("not-an-email", LINK),
    ("person@example.com", ORIGIN + "/verify-email?token=" + TOKEN),
    ("person@example.com", LINK + "\nInjected: true"),
    ("person@example.com", "https://evil.example/verify-email#token=" + TOKEN),
])
def test_invalid_message_data_never_connects(monkeypatch, recipient, link):
    connector = Mock(side_effect=AssertionError("SMTP connection forbidden"))
    monkeypatch.setattr("app.email_delivery.smtplib.SMTP", connector)
    with pytest.raises(EmailDeliveryError, match="Verification delivery unavailable"):
        sender().send_verification(recipient, link, datetime.now(UTC))
    connector.assert_not_called()


@pytest.mark.parametrize("change", [
    {"EMAIL_DELIVERY_MODE": "bad"}, {"SMTP_HOST": "smtp.example.test/path"},
    {"SMTP_PORT": "0"}, {"SMTP_PORT": "65536"},
    {"SMTP_USERNAME": "bad\nuser"}, {"SMTP_PASSWORD": ""},
    {"SMTP_FROM_EMAIL": "Verify <verify@example.com>"},
    {"SMTP_FROM_EMAIL": "verify@example.com\nBcc:evil@example.com"},
    {"SMTP_TIMEOUT_SECONDS": "0"}, {"SMTP_TIMEOUT_SECONDS": "11"},
    {"VERIFICATION_FRONTEND_ORIGIN": ""},
])
def test_invalid_configuration_is_sanitized(change, caplog):
    with pytest.raises(AuthConfigurationError) as captured:
        AuthSettings.from_environment(smtp_values() | change)
    assert SECRET not in str(captured.value) + repr(captured.value) + caplog.text
    assert "Bcc:evil@example.com" not in str(captured.value) + caplog.text


def test_mode_none_is_explicit_and_preview_is_not_a_sender():
    assert build_email_sender(AuthSettings.from_environment({})) is None
    with pytest.raises(AuthConfigurationError):
        AuthSettings.from_environment({"SMTP_PASSWORD": SECRET})
    preview = AuthSettings.from_environment({
        "DEV_EMAIL_VERIFICATION_PREVIEW": "true",
        "VERIFICATION_FRONTEND_ORIGIN": "http://localhost:5173",
    })
    assert build_email_sender(preview) is None


def test_production_startup_configuration_fails_closed_without_sender():
    production = {
        "APP_ENV": "production", "CSRF_HMAC_SECRET": "test-only-secret-12345678901234567890",
        "AUTH_ALLOWED_ORIGINS": ORIGIN,
    }
    with pytest.raises(AuthConfigurationError, match="EMAIL_DELIVERY_MODE"):
        build_email_sender(AuthSettings.from_environment(production))
    with pytest.raises(AuthConfigurationError):
        AuthSettings.from_environment(production | smtp_values() | {
            "VERIFICATION_FRONTEND_ORIGIN": "http://tracker.example.com",
        })
    configured = AuthSettings.from_environment(production | smtp_values())
    assert isinstance(build_email_sender(configured), SmtpEmailSender)
    assert SECRET not in repr(configured) + repr(configured.smtp) + repr(build_email_sender(configured))
    with pytest.raises(AuthConfigurationError, match="VERIFICATION_FRONTEND_ORIGIN"):
        build_email_sender(replace(configured, verification_frontend_origin="http://tracker.example.com"))
    with pytest.raises(AuthConfigurationError, match="VERIFICATION_FRONTEND_ORIGIN"):
        build_email_sender(replace(configured, allowed_browser_origins=("https://other.example.com",)))


def test_actual_production_import_rejects_missing_delivery_without_connections():
    environment = os.environ.copy()
    for name in ("SMTP_HOST", "SMTP_PORT", "SMTP_USERNAME", "SMTP_PASSWORD",
                 "SMTP_FROM_EMAIL", "SMTP_TIMEOUT_SECONDS"):
        environment.pop(name, None)
    environment.update({
        "APP_ENV": "production", "APP_DATABASE_NAME": "jobtracker_auth_ci",
        "CSRF_HMAC_SECRET": "test-only-secret-12345678901234567890",
        "AUTH_ALLOWED_ORIGINS": ORIGIN, "EMAIL_DELIVERY_MODE": "none",
    })
    result = subprocess.run([sys.executable, "-c",
                             "import app.auth_config as config; config.load_dotenv = lambda *a, **k: None; import app.main"],
                            env=environment,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode != 0
    assert "EMAIL_DELIVERY_MODE=smtp is required in production" in result.stderr
    assert SECRET not in result.stderr + result.stdout


def test_actual_production_import_rejects_local_capture_without_connections():
    environment = os.environ.copy()
    environment.update({
        "APP_ENV": "production", "APP_DATABASE_NAME": "jobtracker_auth_ci",
        "CSRF_HMAC_SECRET": "test-only-secret-12345678901234567890",
        "AUTH_ALLOWED_ORIGINS": ORIGIN, "EMAIL_DELIVERY_MODE": "local-capture",
    })
    result = subprocess.run([sys.executable, "-c",
                             "import app.auth_config as config; config.load_dotenv = lambda *a, **k: None; import app.main"],
                            env=environment, capture_output=True, text=True, timeout=15)
    assert result.returncode != 0
    assert "EMAIL_DELIVERY_MODE=local-capture is forbidden in production" in result.stderr
    assert SECRET not in result.stderr + result.stdout
