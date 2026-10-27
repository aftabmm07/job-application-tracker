import pytest
from argon2 import Type
from pwdlib.hashers.argon2 import Argon2Hasher

from app.passwords import (
    PasswordValidationError, hash_password, password_hash_needs_upgrade,
    validate_password, verify_and_upgrade_password, verify_password,
)


@pytest.fixture(scope="module")
def current_hash():
    return hash_password("a long test password")


def test_hash_is_explicit_argon2id(current_hash):
    assert current_hash.startswith("$argon2id$v=19$m=65536,t=3,p=4$")
    assert "a long test password" not in current_hash
    assert not password_hash_needs_upgrade(current_hash)


def test_verification_and_no_upgrade_for_current_hash(current_hash):
    assert verify_password("a long test password", current_hash)
    assert not verify_password("the wrong test password", current_hash)
    assert verify_and_upgrade_password("a long test password", current_hash) == (True, None)
    assert verify_and_upgrade_password("the wrong test password", current_hash) == (False, None)


def test_random_salts(current_hash):
    other = hash_password("a long test password")
    assert other != current_hash
    assert other.split("$")[4] != current_hash.split("$")[4]
    assert verify_password("a long test password", other)


@pytest.mark.parametrize("password", ["x" * 15, "x" * 128, "密碼🔒" * 5, " " * 15])
def test_valid_password_boundaries_and_unicode(password):
    validate_password(password)
    assert verify_password(password, hash_password(password))


def test_no_trimming_truncation_or_unicode_normalization():
    password = "  spaced cafe\u0301 password  "
    encoded = hash_password(password)
    assert verify_password(password, encoded)
    assert not verify_password(password.strip(), encoded)
    assert not verify_password(password.replace("e\u0301", "é"), encoded)
    maximum = "x" * 128
    assert not verify_password(maximum + "y", hash_password(maximum))


@pytest.mark.parametrize("password", ["", "x" * 14, "x" * 129, None, 123, b"a long test password", "\ud800" * 15])
def test_invalid_passwords_do_not_leak_values(password, caplog):
    with pytest.raises(PasswordValidationError) as error:
        hash_password(password)
    assert error.value.args in [
        ("Password must be a string",),
        ("Password must contain between 15 and 128 characters",),
        ("Password must be valid Unicode",),
    ]
    assert caplog.text == ""


def test_validation_failure_returns_false_when_verifying(current_hash):
    assert not verify_password("short", current_hash)
    assert verify_and_upgrade_password("short", current_hash) == (False, None)


@pytest.mark.parametrize("stored_hash", ["not-a-hash", "$argon2id$invalid", "", None])
def test_corrupt_hashes_fail_safely(stored_hash):
    assert not verify_password("a long test password", stored_hash)
    assert verify_and_upgrade_password("a long test password", stored_hash) == (False, None)
    assert password_hash_needs_upgrade(stored_hash)


def test_parameter_upgrade_only_after_successful_verification():
    old_hash = Argon2Hasher(type=Type.ID, memory_cost=8192, time_cost=1, parallelism=1).hash("a long test password")
    assert password_hash_needs_upgrade(old_hash)
    assert verify_and_upgrade_password("the wrong test password", old_hash) == (False, None)
    valid, replacement = verify_and_upgrade_password("a long test password", old_hash)
    assert valid
    assert replacement is not None
    assert replacement.startswith("$argon2id$")
    assert verify_password("a long test password", replacement)
    assert not password_hash_needs_upgrade(replacement)
