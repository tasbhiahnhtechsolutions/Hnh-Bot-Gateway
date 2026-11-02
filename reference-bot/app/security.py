"""Gateway signature check (ai-contract.md section 5).

``signature = HMAC_SHA256(bot_secret, timestamp + "." + raw_body)``, header ``v1=<hex>``.
During a secret rotation the header holds several comma separated signatures: any one may match.
"""

import hashlib
import hmac
import time
from collections.abc import Mapping


class SignatureError(Exception):
    """The request must be answered with HTTP 401 and nothing else."""


def compute_signature(secret: str, timestamp: int, raw_body: bytes) -> str:
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + raw_body, hashlib.sha256)
    return mac.hexdigest()


def verify_signature(
    secret: str,
    headers: Mapping[str, str],
    raw_body: bytes,
    *,
    tolerance: int = 300,
    now: float | None = None,
) -> None:
    """Raise ``SignatureError`` unless the request really comes from the gateway.

    ``headers`` must be case-insensitive (Starlette's ``request.headers`` is).
    ``raw_body`` must be the exact bytes received, never re-serialised JSON.
    """
    try:
        timestamp = int(headers["X-Gateway-Timestamp"])
        header = headers["X-Gateway-Signature"]
    except (KeyError, ValueError) as exc:
        raise SignatureError("missing or malformed signature headers") from exc

    if abs((time.time() if now is None else now) - timestamp) > tolerance:
        raise SignatureError("stale request")

    expected = compute_signature(secret, timestamp, raw_body)
    given = [part.strip().removeprefix("v1=") for part in header.split(",")]
    # Check every candidate (no early exit on the first hit) and compare in constant time.
    matches = [hmac.compare_digest(expected, candidate) for candidate in given]
    if not any(matches):
        raise SignatureError("bad signature")
