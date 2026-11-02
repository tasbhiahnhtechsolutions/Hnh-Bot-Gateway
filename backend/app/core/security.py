"""API key utilities and secret encryption (plan sections 4 and 5)."""

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from enum import StrEnum

from cryptography.fernet import Fernet, InvalidToken


class KeyKind(StrEnum):
    LIVE = "live"
    TEST = "test"


@dataclass(frozen=True, slots=True)
class GeneratedApiKey:
    full_key: str  # show to the admin exactly once
    prefix: str  # stored in plain text, used for lookup
    key_hash: str  # sha256 hex, the only thing stored about the secret part


@dataclass(frozen=True, slots=True)
class ParsedApiKey:
    kind: KeyKind
    prefix: str


def generate_api_key(kind: KeyKind = KeyKind.LIVE) -> GeneratedApiKey:
    prefix = secrets.token_hex(4)  # 8 hex chars
    secret = secrets.token_urlsafe(32)
    full_key = f"wab_{kind.value}_{prefix}_{secret}"
    return GeneratedApiKey(full_key=full_key, prefix=prefix, key_hash=hash_api_key(full_key))


def hash_api_key(full_key: str) -> str:
    # SHA-256 is enough: the key is long and random, so it cannot be guessed (plan section 4).
    return hashlib.sha256(full_key.encode()).hexdigest()


def verify_api_key(full_key: str, stored_hash: str) -> bool:
    return hmac.compare_digest(hash_api_key(full_key), stored_hash)


def parse_api_key(full_key: str) -> ParsedApiKey | None:
    """Split ``wab_<kind>_<prefix>_<secret>``. Returns None if the shape is wrong.

    The secret part may itself contain ``_`` (urlsafe base64), so we split at most 3 times.
    """
    parts = full_key.split("_", 3)
    if len(parts) != 4 or parts[0] != "wab" or not parts[2] or not parts[3]:
        return None
    try:
        kind = KeyKind(parts[1])
    except ValueError:
        return None
    return ParsedApiKey(kind=kind, prefix=parts[2])


def generate_signing_secret() -> str:
    """Bot secret / webhook secret (``whsec_...``), shown to the admin once."""
    return f"whsec_{secrets.token_urlsafe(32)}"


class SecretBox:
    """Encrypts secrets that must be readable again (HMAC signing needs the real value)."""

    def __init__(self, key: str | bytes) -> None:
        self._fernet = Fernet(key)

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, token: str) -> str:
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except InvalidToken as exc:
            raise ValueError("cannot decrypt secret: wrong key or corrupted value") from exc
