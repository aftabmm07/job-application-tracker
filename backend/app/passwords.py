"""Password policy and Argon2id helpers; callers must never log passwords."""

from argon2 import Type
from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError
from pwdlib.hashers.argon2 import Argon2Hasher

MIN_PASSWORD_LENGTH = 15
MAX_PASSWORD_LENGTH = 128

# Explicit Argon2id with 64 MiB memory, three passes and independent random salts.
_argon2 = Argon2Hasher(type=Type.ID, memory_cost=65536, time_cost=3, parallelism=4)
_password_hash = PasswordHash((_argon2,))


class PasswordValidationError(ValueError):
    """Messages deliberately exclude the submitted password."""


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
    return _password_hash.hash(password)


def verify_password(password: str, stored_hash: str) -> bool:
    """Invalid passwords or unrecognized/corrupt hashes fail without exposing input."""
    try:
        validate_password(password)
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
        return _password_hash.verify_and_update(password, stored_hash)
    except (PasswordValidationError, UnknownHashError, ValueError, TypeError):
        return False, None
