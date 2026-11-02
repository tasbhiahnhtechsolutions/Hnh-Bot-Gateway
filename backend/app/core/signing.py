"""Gateway signature (ai-contract.md section 5, plan section 17.A.5).

``signature = HMAC_SHA256(secret, f"{timestamp}." + raw_body)``, sent as ``v1=<hex>``.
During a secret rotation the header holds two comma separated signatures.
"""

import hashlib
import hmac
import time
from collections.abc import Mapping, Sequence

SIGNATURE_VERSION = "v1"
TOLERANCE_SECONDS = 300


def compute_signature(secret: str, timestamp: int, raw_body: bytes) -> str:
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + raw_body, hashlib.sha256)
    return mac.hexdigest()


def signature_header(secrets: Sequence[str], timestamp: int, raw_body: bytes) -> str:
    """``v1=<new>,v1=<old>``: pass the new secret first while a rotation is running."""
    return ",".join(
        f"{SIGNATURE_VERSION}={compute_signature(s, timestamp, raw_body)}" for s in secrets
    )


class SignatureError(Exception):
    """The request must be answered with HTTP 401."""


def verify_signature(
    secret: str,
    headers: Mapping[str, str],
    raw_body: bytes,
    *,
    tolerance: int = TOLERANCE_SECONDS,
    now: float | None = None,
) -> None:
    """Python version of the check vendors must do. Raises ``SignatureError`` on any problem."""
    try:
        timestamp = int(headers["X-Gateway-Timestamp"])
        header = headers["X-Gateway-Signature"]
    except (KeyError, ValueError) as exc:
        raise SignatureError("missing or malformed signature headers") from exc
    if abs((time.time() if now is None else now) - timestamp) > tolerance:
        raise SignatureError("stale request")
    expected = compute_signature(secret, timestamp, raw_body)
    given = [part.strip().removeprefix(f"{SIGNATURE_VERSION}=") for part in header.split(",")]
    if not any(hmac.compare_digest(expected, g) for g in given):
        raise SignatureError("bad signature")
