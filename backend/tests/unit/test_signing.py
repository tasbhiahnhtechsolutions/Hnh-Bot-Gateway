import hashlib
import hmac
import shutil
import subprocess
import time

import pytest

from app.core.signing import (
    SignatureError,
    compute_signature,
    signature_header,
    verify_signature,
)

SECRET = "whsec_test_secret"
NOW = 1_730_300_000


def headers_for(body: bytes, secret: str = SECRET, ts: int = NOW) -> dict[str, str]:
    return {
        "X-Gateway-Timestamp": str(ts),
        "X-Gateway-Signature": signature_header([secret], ts, body),
    }


def contract_sample_signature(secret: str, ts: int, raw_body: bytes) -> str:
    """The exact formula from ai-contract.md section 5.3, written independently."""
    return hmac.new(secret.encode(), f"{ts}.".encode() + raw_body, hashlib.sha256).hexdigest()


BODIES = [
    b'{"a":1}',
    "سلام 👋 کیا حال ہے".encode(),
    b'{"text":"line1\\nline2"}\n',
    b"x" * (30 * 1024 * 1024),
    b"",
]


@pytest.mark.parametrize("body", BODIES, ids=["json", "urdu-emoji", "newline", "30mb", "empty"])
def test_matches_the_contract_formula_and_verifies(body: bytes) -> None:
    assert compute_signature(SECRET, NOW, body) == contract_sample_signature(SECRET, NOW, body)
    verify_signature(SECRET, headers_for(body), body, now=NOW)


def test_header_format() -> None:
    header = signature_header([SECRET], NOW, b"{}")
    assert header.startswith("v1=")
    assert "," not in header


def test_one_changed_byte_fails() -> None:
    body = b'{"amount":100}'
    with pytest.raises(SignatureError, match="bad signature"):
        verify_signature(SECRET, headers_for(body), b'{"amount":101}', now=NOW)


def test_wrong_secret_fails() -> None:
    body = b"{}"
    with pytest.raises(SignatureError, match="bad signature"):
        verify_signature("whsec_other", headers_for(body), body, now=NOW)


def test_reserialized_json_fails() -> None:
    """The most common vendor mistake: parse, then stringify again (spacing changes)."""
    original = b'{"a":1,"b":2}'
    with pytest.raises(SignatureError):
        verify_signature(SECRET, headers_for(original), b'{"a": 1, "b": 2}', now=NOW)


@pytest.mark.parametrize(
    ("offset", "ok"),
    [
        (0, True),
        (299, True),
        (-299, True),
        (301, False),
        (-301, False),
        (600, False),
        (-600, False),
    ],
)
def test_timestamp_tolerance_is_300_seconds_both_ways(offset: int, ok: bool) -> None:
    body = b"{}"
    headers = headers_for(body, ts=NOW)
    if ok:
        verify_signature(SECRET, headers, body, now=NOW + offset)
    else:
        with pytest.raises(SignatureError, match="stale"):
            verify_signature(SECRET, headers, body, now=NOW + offset)


def test_six_minute_old_request_is_rejected_with_real_clock() -> None:
    body = b"{}"
    old = int(time.time()) - 360
    with pytest.raises(SignatureError, match="stale"):
        verify_signature(SECRET, headers_for(body, ts=old), body)


def test_rotation_accepts_new_or_old_secret() -> None:
    body = b'{"x":1}'
    header = signature_header(["whsec_new", "whsec_old"], NOW, body)
    assert header.count("v1=") == 2
    headers = {"X-Gateway-Timestamp": str(NOW), "X-Gateway-Signature": header}
    verify_signature("whsec_new", headers, body, now=NOW)
    verify_signature("whsec_old", headers, body, now=NOW)
    with pytest.raises(SignatureError):
        verify_signature("whsec_third", headers, body, now=NOW)


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"X-Gateway-Timestamp": "abc", "X-Gateway-Signature": "v1=00"},
        {"X-Gateway-Timestamp": "1"},
    ],
)
def test_missing_or_malformed_headers_fail(headers: dict[str, str]) -> None:
    with pytest.raises(SignatureError):
        verify_signature(SECRET, headers, b"{}", now=NOW)


NODE_VERIFIER = r"""
// The verification code of ai-contract.md section 5.4, as a plain function.
const crypto = require('crypto');
const [secret, ts, header, bodyHex] = process.argv.slice(1);
const body = Buffer.from(bodyHex, 'hex');
const expected = crypto.createHmac('sha256', secret).update(`${ts}.`).update(body).digest('hex');
const given = header.split(',').map(s => s.trim().replace('v1=', ''));
const ok = given.some(g => g.length === expected.length &&
  crypto.timingSafeEqual(Buffer.from(g), Buffer.from(expected)));
process.exit(ok ? 0 : 1);
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.parametrize("body", BODIES[:3], ids=["json", "urdu-emoji", "newline"])
def test_node_verifier_from_the_contract_accepts_our_signature(body: bytes) -> None:
    header = signature_header(["whsec_new", SECRET], NOW, body)

    def run(secret: str, payload: bytes) -> int:
        return subprocess.run(  # noqa: S603
            ["node", "-e", NODE_VERIFIER, secret, str(NOW), header, payload.hex()],  # noqa: S607
            check=False,
        ).returncode

    assert run(SECRET, body) == 0
    assert run("whsec_new", body) == 0
    assert run("whsec_wrong", body) == 1
    assert run(SECRET, body + b" ") == 1
