"""Plan section 14.2: the gateway signs in Python, vendors verify in Node and in Python.

Needs ``node`` (always present in CI; skipped on a machine without it).
Three independent implementations must agree: the gateway (``app.core.signing``), a Node
vendor (``node_verifier.mjs``) and the reference bot (``reference-bot/app/security.py``).
"""

import importlib.util
import json
import re
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

from app.core.signing import SignatureError, signature_header, verify_signature

HERE = Path(__file__).parent
PROJECT = HERE.parents[2]  # whatsup-plugin/
NODE_VERIFIER = HERE / "node_verifier.mjs"
NODE_SIGNER = HERE / "node_signer.mjs"
CONTRACT = PROJECT / "ai-contract.md"
REFERENCE_BOT_SECURITY = PROJECT / "reference-bot" / "app" / "security.py"

SECRET = "whsec_cross_language_secret"
NOW = 1_730_300_000

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")

BODIES = {
    "json": b'{"chatId":"923001234567@c.us","incomingMessages":[{"text":"hi"}]}',
    "urdu": '{"text":"سلام، کیا حال ہے؟ قیمت بتائیں"}'.encode(),
    "emoji": '{"text":"👋🏽 hello 🎉 \\u00e9"}'.encode(),
    "newlines": b'{\n  "text": "line1\\nline2"\n}\n\r\n',
    "binary-ish": bytes(range(256)),
    "empty": b"",
    "30mb": b'{"pad":"' + b"a" * (30 * 1024 * 1024) + b'"}',
}


def node_accepts(
    tmp_path: Path,
    body: bytes,
    header: str,
    *,
    secret: str = SECRET,
    ts: int = NOW,
    now: int = NOW,
) -> bool:
    body_file = tmp_path / "body.bin"
    body_file.write_bytes(body)
    result = subprocess.run(  # noqa: S603
        ["node", str(NODE_VERIFIER), secret, str(ts), header, str(body_file), str(now)],  # noqa: S607
        check=False,
        capture_output=True,
    )
    assert result.returncode in (0, 1), result.stderr.decode()
    return result.returncode == 0


def load_reference_bot_security() -> ModuleType:
    spec = importlib.util.spec_from_file_location("reference_bot_security", REFERENCE_BOT_SECURITY)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- gateway (Python) signs, Node verifies ---------------------------------------------------


@pytest.mark.parametrize("name", list(BODIES))
def test_node_vendor_accepts_what_the_gateway_signs(name: str, tmp_path: Path) -> None:
    body = BODIES[name]
    header = signature_header([SECRET], NOW, body)
    assert node_accepts(tmp_path, body, header)


def test_node_vendor_rejects_a_changed_byte_and_the_wrong_secret(tmp_path: Path) -> None:
    body = BODIES["urdu"]
    header = signature_header([SECRET], NOW, body)
    flipped = bytearray(body)
    flipped[-3] ^= 0x01
    assert not node_accepts(tmp_path, bytes(flipped), header)
    assert not node_accepts(tmp_path, body, header, secret="whsec_wrong")
    assert not node_accepts(tmp_path, body + b" ", header)


def test_node_vendor_rejects_a_big_body_with_one_changed_byte(tmp_path: Path) -> None:
    body = BODIES["30mb"]
    header = signature_header([SECRET], NOW, body)
    changed = bytearray(body)
    changed[len(changed) // 2] ^= 0x01
    assert node_accepts(tmp_path, body, header)
    assert not node_accepts(tmp_path, bytes(changed), header)


def test_node_vendor_rejects_reserialized_json(tmp_path: Path) -> None:
    """The most common vendor bug: parse the body, stringify it again, verify that."""
    original = b'{"chatId":"1@c.us","incomingMessages":[{"text":"hi","media":null}]}'
    header = signature_header([SECRET], NOW, original)
    for rewritten in (
        json.dumps(json.loads(original)).encode(),  # spaces after : and ,
        json.dumps(json.loads(original), indent=2).encode(),
        json.dumps(json.loads(original), sort_keys=True, separators=(",", ":")).encode(),
    ):
        if rewritten != original:
            assert not node_accepts(tmp_path, rewritten, header)


@pytest.mark.parametrize(
    ("offset", "ok"),
    [
        (0, True),
        (-299, True),
        (299, True),
        (-360, False),
        (360, False),
        (600, False),
        (-600, False),
    ],
)
def test_node_vendor_timestamp_window(offset: int, ok: bool, tmp_path: Path) -> None:
    body = BODIES["json"]
    header = signature_header([SECRET], NOW, body)
    assert node_accepts(tmp_path, body, header, now=NOW + offset) is ok


def test_node_vendor_during_secret_rotation(tmp_path: Path) -> None:
    body = BODIES["emoji"]
    header = signature_header(["whsec_new", "whsec_old"], NOW, body)
    assert header.count("v1=") == 2
    assert node_accepts(tmp_path, body, header, secret="whsec_new")
    assert node_accepts(tmp_path, body, header, secret="whsec_old")
    assert not node_accepts(tmp_path, body, header, secret="whsec_third")


# --- Node signs, the gateway's Python verifier and the reference bot verify ------------------


def node_sign(tmp_path: Path, body: bytes, secret: str = SECRET, ts: int = NOW) -> str:
    body_file = tmp_path / "body.bin"
    body_file.write_bytes(body)
    result = subprocess.run(  # noqa: S603
        ["node", str(NODE_SIGNER), secret, str(ts), str(body_file)],  # noqa: S607
        check=True,
        capture_output=True,
    )
    return result.stdout.decode()


@pytest.mark.parametrize("name", ["json", "urdu", "emoji", "newlines", "binary-ish", "empty"])
def test_python_and_reference_bot_accept_a_node_signature(name: str, tmp_path: Path) -> None:
    body = BODIES[name]
    header = node_sign(tmp_path, body)
    assert header == signature_header([SECRET], NOW, body)  # byte-for-byte the same
    headers = {"X-Gateway-Timestamp": str(NOW), "X-Gateway-Signature": header}

    verify_signature(SECRET, headers, body, now=NOW)
    bot = load_reference_bot_security()
    bot.verify_signature(SECRET, headers, body, now=NOW)

    with pytest.raises(SignatureError):
        verify_signature("whsec_wrong", headers, body, now=NOW)
    with pytest.raises(bot.SignatureError):
        bot.verify_signature("whsec_wrong", headers, body, now=NOW)


def test_reference_bot_agrees_with_the_gateway_on_every_rejection() -> None:
    bot = load_reference_bot_security()
    body = BODIES["json"]
    good = {
        "X-Gateway-Timestamp": str(NOW),
        "X-Gateway-Signature": signature_header([SECRET], NOW, body),
    }
    cases: list[tuple[dict[str, str], bytes, str, int]] = [
        (good, body + b"x", SECRET, NOW),  # changed body
        (good, body, "whsec_other", NOW),  # wrong secret
        (good, body, SECRET, NOW + 301),  # stale
        (good, body, SECRET, NOW - 301),  # from the future
        ({}, body, SECRET, NOW),  # no headers
        ({**good, "X-Gateway-Timestamp": "x"}, body, SECRET, NOW),  # junk timestamp
    ]
    for headers, raw, secret, now in cases:
        with pytest.raises(SignatureError):
            verify_signature(secret, headers, raw, now=now)
        with pytest.raises(bot.SignatureError):
            bot.verify_signature(secret, headers, raw, now=now)


def test_the_documented_python_verifier_accepts_our_signature() -> None:
    """Run the code block of ai-contract.md section 5.3 as it is written in the document."""
    text = CONTRACT.read_text(encoding="utf-8")
    section = text.split("### 5.3 Python example", 1)[1].split("### 5.4", 1)[0]
    match = re.search(r"```python\n(.*?)```", section, re.S)
    assert match, "no python block in section 5.3 of the contract"
    namespace: dict[str, object] = {}
    exec(match.group(1), namespace)  # noqa: S102  (our own document, test only)
    verify = namespace["verify_gateway_signature"]
    assert callable(verify)
    check: Callable[..., None] = verify

    for body in (BODIES["json"], BODIES["urdu"], BODIES["newlines"]):
        ts = int(time.time())
        headers = {
            "X-Gateway-Timestamp": str(ts),
            "X-Gateway-Signature": signature_header(["whsec_new", SECRET], ts, body),
        }
        check(SECRET, headers, body)
        with pytest.raises(PermissionError):
            check("whsec_nope", headers, body)
        with pytest.raises(PermissionError):
            check(SECRET, headers, body + b"!")

    old = int(time.time()) - 360
    stale = {
        "X-Gateway-Timestamp": str(old),
        "X-Gateway-Signature": signature_header([SECRET], old, b"{}"),
    }
    with pytest.raises(PermissionError, match="stale"):
        check(SECRET, stale, b"{}")
