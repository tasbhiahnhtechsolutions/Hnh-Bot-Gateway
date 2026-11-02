"""Admin password hashing: argon2id with the OWASP minimum parameters (plan section 9.3)."""

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

MIN_PASSWORD_LENGTH = 12

_hasher = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)
# Checked when the e-mail is unknown, so a wrong e-mail and a wrong password take equally long.
_DUMMY_HASH = _hasher.hash("not-a-real-password")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(stored_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(stored_hash or _DUMMY_HASH, password) and stored_hash is not None
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(stored_hash: str) -> bool:
    return _hasher.check_needs_rehash(stored_hash)
