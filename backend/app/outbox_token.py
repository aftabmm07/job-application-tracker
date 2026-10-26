"""Pure token derivation for a future outbox worker; not wired into issuance."""
import base64
import hashlib
import hmac
import re


_PURPOSE = b"job-application-tracker/email-verification-outbox/v1\x00"


def derive_verification_token(secret: bytes, *, token_id: int, nonce: bytes, key_version: str) -> str:
    """Return a 43-character bearer without storing it. Use a dedicated random key.

    A byte-length check cannot prove entropy. Operators must eventually supply a
    CSPRNG-generated key, separately from session and CSRF secrets.
    """
    if (not isinstance(secret, bytes) or len(secret) < 32 or len(secret) > 1024
            or len(set(secret)) < 2):
        raise ValueError("Invalid outbox derivation key")
    if isinstance(token_id, bool) or not isinstance(token_id, int) or not 1 <= token_id <= 2**31 - 1:
        raise ValueError("Invalid verification token ID")
    if not isinstance(nonce, bytes) or len(nonce) != 32:
        raise ValueError("Invalid outbox nonce")
    if not isinstance(key_version, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", key_version):
        raise ValueError("Invalid outbox key version")
    version = key_version.encode("ascii")
    # Fixed-width token ID/nonce and an explicit version length prevent ambiguous
    # field boundaries; the purpose excludes other HMAC uses of this key.
    message = _PURPOSE + len(version).to_bytes(1, "big") + version + token_id.to_bytes(4, "big") + nonce
    derived = hmac.new(secret, message, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(derived).rstrip(b"=").decode("ascii")
