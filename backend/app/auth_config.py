"""Validated authentication settings. No database access or auth endpoints."""

import os
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv


class AuthConfigurationError(ValueError):
    """Configuration errors contain setting names, never supplied values."""


def _positive_integer(values, name, default, maximum):
    raw = values.get(name, str(default))
    if not re.fullmatch(r"[0-9]{1,9}", raw):
        raise AuthConfigurationError(f"{name} must be a positive integer")
    value = int(raw)
    if not 1 <= value <= maximum:
        raise AuthConfigurationError(f"{name} is outside the supported range")
    return value


def normalize_browser_origin(value, production):
    try:
        parsed = urlsplit(value)
        port = parsed.port
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
            or any(marker in value for marker in ("*", "?", "#", "\\"))
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
            or any(character.isspace() for character in value)
            or parsed.netloc.endswith(":")
            or (port is not None and port < 1)
            or (production and parsed.scheme != "https")
        ):
            raise ValueError
        # Reject URL punctuation in hostnames; allow DNS, IPv4 and bracketed IPv6.
        if not re.fullmatch(r"[a-zA-Z0-9.:-]+", parsed.hostname):
            raise ValueError
        host = parsed.hostname.lower()
        if ":" in host:
            host = f"[{host}]"
        default_port = 443 if parsed.scheme == "https" else 80
        suffix = f":{port}" if port is not None and port != default_port else ""
        return f"{parsed.scheme}://{host}{suffix}"
    except ValueError:
        raise AuthConfigurationError("AUTH_ALLOWED_ORIGINS must contain exact browser origins; production requires HTTPS") from None


@dataclass(frozen=True)
class AuthSettings:
    environment: str
    session_cookie_name: str
    session_cookie_secure: bool
    session_absolute_timeout_seconds: int
    session_idle_timeout_seconds: int
    csrf_hmac_secret: bytes = field(repr=False)
    allowed_browser_origins: tuple[str, ...]
    password_hash_max_concurrency: int = 2
    json_max_body_bytes: int = 65536

    @classmethod
    def from_environment(cls, values: Mapping[str, str]):
        environment = values.get("APP_ENV", "development").strip().lower()
        if environment not in {"development", "production"}:
            raise AuthConfigurationError("APP_ENV must be development or production")
        production = environment == "production"

        secure = values.get("SESSION_COOKIE_SECURE", "true" if production else "false").strip().lower()
        if secure not in {"true", "false"}:
            raise AuthConfigurationError("SESSION_COOKIE_SECURE must be true or false")
        secure = secure == "true"
        if production and not secure:
            raise AuthConfigurationError("SESSION_COOKIE_SECURE must be true in production")

        name = values.get("SESSION_COOKIE_NAME", "__Host-session" if production else "session")
        if not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}", name):
            raise AuthConfigurationError("SESSION_COOKIE_NAME must be a valid cookie name of at most 128 characters")
        if name.startswith(("__Host-", "__Secure-")) and not secure:
            raise AuthConfigurationError("SESSION_COOKIE_NAME uses a prefix that requires Secure")
        if production and not name.startswith("__Host-"):
            raise AuthConfigurationError("SESSION_COOKIE_NAME must use the __Host- prefix in production")

        absolute = _positive_integer(values, "SESSION_ABSOLUTE_TIMEOUT_SECONDS", 28800, 604800)
        idle = _positive_integer(values, "SESSION_IDLE_TIMEOUT_SECONDS", 1800, 86400)
        if idle > absolute:
            raise AuthConfigurationError("SESSION_IDLE_TIMEOUT_SECONDS must not exceed the absolute timeout")

        secret = values.get("CSRF_HMAC_SECRET")
        if secret is None:
            if production:
                raise AuthConfigurationError("CSRF_HMAC_SECRET is required in production")
            secret_bytes = secrets.token_bytes(32)
        else:
            try:
                secret_bytes = secret.encode("utf-8")
            except UnicodeError:
                raise AuthConfigurationError("CSRF_HMAC_SECRET must be valid UTF-8") from None
            if not 32 <= len(secret_bytes) <= 1024 or secret.isspace():
                raise AuthConfigurationError("CSRF_HMAC_SECRET must contain 32 to 1024 UTF-8 bytes")

        origins = values.get("AUTH_ALLOWED_ORIGINS")
        if origins is None:
            if production:
                raise AuthConfigurationError("AUTH_ALLOWED_ORIGINS is required in production")
            origins = "http://localhost:5173,http://127.0.0.1:5173"
        entries = [entry.strip() for entry in origins.split(",")]
        if any(not entry for entry in entries):
            raise AuthConfigurationError("AUTH_ALLOWED_ORIGINS must not contain empty entries")
        allowed_origins = tuple(dict.fromkeys(normalize_browser_origin(entry, production) for entry in entries))
        hash_concurrency = _positive_integer(values, "PASSWORD_HASH_MAX_CONCURRENCY", 2, 8)
        body_bytes = _positive_integer(values, "JSON_MAX_BODY_BYTES", 65536, 1048576)
        return cls(environment, name, secure, absolute, idle, secret_bytes, allowed_origins,
                   hash_concurrency, body_bytes)


@lru_cache(maxsize=1)
def get_auth_settings():
    # Match existing .env behavior: exported environment variables take precedence.
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    return AuthSettings.from_environment(os.environ)
