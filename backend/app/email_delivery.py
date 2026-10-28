"""Replaceable verification-email boundary with production SMTP and local capture."""

from datetime import datetime
from email.message import EmailMessage
import re
import smtplib
import ssl
from typing import Protocol

from email_validator import validate_email
from fastapi import Request

from app.auth_config import (
    AuthConfigurationError, AuthSettings, LOCAL_CAPTURE_FRONTEND_ORIGINS,
    LOCAL_CAPTURE_SMTP_HOST, LOCAL_CAPTURE_SMTP_PORT,
    STACK_CAPTURE_FRONTEND_ORIGIN, STACK_CAPTURE_SMTP_HOST, STACK_CAPTURE_SMTP_PORT,
)


class EmailDeliveryError(RuntimeError):
    """A sanitized failure; SMTP exception text may contain credentials or links."""


class EmailSender(Protocol):
    def send_verification(self, recipient: str, link: str, expires_at: datetime) -> None:
        """Return after accepting delivery, or raise on failure.

        Implementations must not log links or include them in exception messages.
        A successful return cannot guarantee that the recipient received the email.
        """


def _verification_message(recipient: str, link: str, expires_at: datetime,
                          origin: str, from_email: str) -> EmailMessage:
    normalized = validate_email(recipient, check_deliverability=False).normalized
    if recipient != normalized.lower() or any(ord(char) < 33 or ord(char) == 127 for char in recipient):
        raise ValueError
    if not re.fullmatch(re.escape(origin) + r"/verify-email#token=[A-Za-z0-9_-]{43}", link):
        raise ValueError
    if expires_at.tzinfo is None or expires_at.utcoffset() is None:
        raise ValueError
    message = EmailMessage()
    message["From"] = from_email
    message["To"] = recipient
    message["Subject"] = "Verify your Job Application Tracker email"
    message.set_content(
        "Use this link to verify your email address:\n\n"
        f"{link}\n\nThis link expires at {expires_at.isoformat()}.\n"
    )
    return message


class SmtpEmailSender:
    def __init__(self, settings: AuthSettings):
        if settings.email_delivery_mode != "smtp" or settings.smtp is None:
            raise AuthConfigurationError("SMTP delivery is not configured")
        if (settings.verification_frontend_origin is None
                or settings.verification_frontend_origin not in settings.allowed_browser_origins
                or (settings.environment == "production"
                    and not settings.verification_frontend_origin.startswith("https://"))):
            raise AuthConfigurationError("VERIFICATION_FRONTEND_ORIGIN is invalid for SMTP delivery")
        self._smtp = settings.smtp
        self._origin = settings.verification_frontend_origin

    def send_verification(self, recipient: str, link: str, expires_at: datetime) -> None:
        try:
            message = _verification_message(recipient, link, expires_at,
                                            self._origin, self._smtp.from_email)
            # SMTP's debug output stays disabled. TLS certificate and hostname
            # verification use the system trust store; there is no fallback.
            with smtplib.SMTP(self._smtp.host, self._smtp.port,
                              timeout=self._smtp.timeout_seconds) as client:
                client.ehlo()
                client.starttls(context=ssl.create_default_context())
                client.ehlo()
                client.login(self._smtp.username, self._smtp.password)
                refused = client.send_message(message)
                if refused:
                    raise EmailDeliveryError("Verification delivery unavailable")
        except Exception:
            # Do not chain SMTP errors: exception args can include message data.
            raise EmailDeliveryError("Verification delivery unavailable") from None


class LocalCaptureEmailSender:
    """Development-only, fixed-loopback SMTP for an ephemeral capture mailbox."""

    def __init__(self, settings: AuthSettings):
        if (settings.environment != "development" or settings.email_delivery_mode != "local-capture"
                or settings.smtp is not None or settings.dev_email_verification_preview
                or settings.verification_frontend_origin not in LOCAL_CAPTURE_FRONTEND_ORIGINS
                or settings.verification_frontend_origin not in settings.allowed_browser_origins):
            raise AuthConfigurationError("Local email capture is not safely configured")
        self._origin = settings.verification_frontend_origin
        self._host = LOCAL_CAPTURE_SMTP_HOST
        self._port = LOCAL_CAPTURE_SMTP_PORT

    def send_verification(self, recipient: str, link: str, expires_at: datetime) -> None:
        try:
            message = _verification_message(recipient, link, expires_at,
                                            self._origin, "verify@jobtracker.test")
            with smtplib.SMTP(self._host, self._port, timeout=5) as client:
                client.ehlo()
                refused = client.send_message(message)
                if refused:
                    raise EmailDeliveryError("Verification delivery unavailable")
        except Exception:
            # Never expose captured bearer links or SMTP error details.
            raise EmailDeliveryError("Verification delivery unavailable") from None


class StackCaptureEmailSender(LocalCaptureEmailSender):
    """Development stack only; SMTP is confined to the Compose mail network."""

    def __init__(self, settings: AuthSettings):
        if (settings.environment != "development" or settings.email_delivery_mode != "stack-capture"
                or settings.smtp is not None or settings.dev_email_verification_preview
                or settings.verification_frontend_origin != STACK_CAPTURE_FRONTEND_ORIGIN
                or settings.allowed_browser_origins != (STACK_CAPTURE_FRONTEND_ORIGIN,)):
            raise AuthConfigurationError("Stack email capture is not safely configured")
        self._origin = STACK_CAPTURE_FRONTEND_ORIGIN
        self._host = STACK_CAPTURE_SMTP_HOST
        self._port = STACK_CAPTURE_SMTP_PORT


def build_email_sender(settings: AuthSettings) -> EmailSender | None:
    """Construct without connecting; production settings reject missing delivery."""
    if settings.email_delivery_mode == "none":
        if settings.environment == "production":
            raise AuthConfigurationError("EMAIL_DELIVERY_MODE=smtp is required in production")
        return None
    if settings.email_delivery_mode == "local-capture":
        return LocalCaptureEmailSender(settings)
    if settings.email_delivery_mode == "stack-capture":
        return StackCaptureEmailSender(settings)
    if settings.email_delivery_mode == "smtp":
        return SmtpEmailSender(settings)
    raise AuthConfigurationError("EMAIL_DELIVERY_MODE is invalid")


def get_email_sender(request: Request) -> EmailSender | None:
    """Resolve a sender explicitly installed by trusted application startup code.

    Tests can override this dependency with a fake sender.
    """
    return getattr(request.app.state, "email_sender", None)
