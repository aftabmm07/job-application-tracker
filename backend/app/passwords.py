"""Password policy and Argon2id helpers; callers must never log passwords."""

from contextlib import contextmanager
from threading import BoundedSemaphore

from argon2 import Type
from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError
from pwdlib.hashers.argon2 import Argon2Hasher

from app.auth_config import get_auth_settings

MIN_PASSWORD_LENGTH = 15
MAX_PASSWORD_LENGTH = 128

# Explicit Argon2id with 64 MiB memory, three passes and independent random salts.
_argon2 = Argon2Hasher(type=Type.ID, memory_cost=65536, time_cost=3, parallelism=4)
_password_hash = PasswordHash((_argon2,))


class PasswordValidationError(ValueError):
    """Messages deliberately exclude the submitted password."""


class PasswordWorkBusy(RuntimeError):
    """No hashing slot is available; contains no credential information."""


class PasswordWorkLimiter:
    """Per-process gate, shared by hashing, verification and hash upgrades."""

    def __init__(self, capacity):
        self.slots = BoundedSemaphore(capacity)

    @contextmanager
    def acquire(self):
        if not self.slots.acquire(blocking=False):
            raise PasswordWorkBusy("Authentication service busy. Please try again later.")
        try:
            yield
        finally:
            # Runs for BaseException/cancellation too. A running worker retains
            # its slot until the actual password operation finishes.
            self.slots.release()


# Initialize once during module import, before concurrent requests can arrive.
_password_work_limiter = PasswordWorkLimiter(get_auth_settings().password_hash_max_concurrency)


def get_password_work_limiter():
    return _password_work_limiter


def validate_password(password: str) -> None:
    """Count Unicode code points; preserve spaces and exact Unicode representation."""
    if not isinstance(password, str):
        raise PasswordValidationError("Password must be a string")
    if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
        raise PasswordValidationError("Password must contain between 15 and 128 characters")
    try:
        password.encode("utf-8")
    except UnicodeError:
        raise PasswordValidationError("Password must be valid Unicode") from None


def hash_password(password: str) -> str:
    validate_password(password)
    with get_password_work_limiter().acquire():
        return _password_hash.hash(password)


def verify_password(password: str, stored_hash: str) -> bool:
    """Invalid passwords or unrecognized/corrupt hashes fail without exposing input."""
    try:
        validate_password(password)
        with get_password_work_limiter().acquire():
            return _password_hash.verify(password, stored_hash)
    except (PasswordValidationError, UnknownHashError, ValueError, TypeError):
        return False


def password_hash_needs_upgrade(stored_hash: str) -> bool:
    """Inspect only a stored hash; a replacement must follow successful verification."""
    try:
        return _argon2.check_needs_rehash(stored_hash)
    except (ValueError, TypeError):
        return True


def verify_and_upgrade_password(password: str, stored_hash: str) -> tuple[bool, str | None]:
    """Return (valid, optional replacement); never upgrade after a failed check."""
    try:
        validate_password(password)
        with get_password_work_limiter().acquire():
            return _password_hash.verify_and_update(password, stored_hash)
    except (PasswordValidationError, UnknownHashError, ValueError, TypeError):
        return False, None
