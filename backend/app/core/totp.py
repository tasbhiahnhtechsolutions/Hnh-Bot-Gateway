"""Time-based one-time passwords (RFC 6238) for admin 2FA: HMAC-SHA1, 30 seconds, 6 digits.

Standard authenticator apps (Google Authenticator, Authy, 1Password, ...) all speak this.
Written with the standard library so there is nothing extra to trust.
"""

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

STEP_SECONDS = 30
DIGITS = 6


def new_secret() -> str:
    """160 random bits as base32 (what authenticator apps expect)."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _key(secret: str) -> bytes:
    padded = secret.upper() + "=" * (-len(secret) % 8)
    return base64.b32decode(padded)


def code_for_step(secret: str, step: int) -> str:
    digest = hmac.new(_key(secret), struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(number % 10**DIGITS).zfill(DIGITS)


def current_step(now: float | None = None) -> int:
    return int((time.time() if now is None else now) // STEP_SECONDS)


def verify(secret: str, code: str, *, now: float | None = None, window: int = 1) -> int | None:
    """The time step the code belongs to (so the caller can refuse a second use), or None.

    ``window`` allows a clock that is that many 30-second steps off.
    """
    code = code.strip().replace(" ", "")
    if len(code) != DIGITS or not (code.isascii() and code.isdigit()):
        return None
    step_now = current_step(now)
    matched: int | None = None
    for step in range(step_now - window, step_now + window + 1):  # no early exit: constant time
        if hmac.compare_digest(code_for_step(secret, step), code):
            matched = step
    return matched


def otpauth_uri(secret: str, account: str, issuer: str) -> str:
    return (
        f"otpauth://totp/{quote(issuer)}:{quote(account)}"
        f"?secret={secret}&issuer={quote(issuer)}&algorithm=SHA1&digits={DIGITS}&period={STEP_SECONDS}"
    )
