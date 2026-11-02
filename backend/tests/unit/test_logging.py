import json

import pytest

from app.core.logging import REDACTED, configure_logging, get_logger

LIVE_KEY = "wab_live_k3f9a1xz_9fA83abcDEF_ghi"


@pytest.fixture(autouse=True)
def _logging() -> None:
    configure_logging("DEBUG", json_logs=True)


def emit(capsys: pytest.CaptureFixture[str], **fields: object) -> dict[str, object]:
    get_logger("t").info("hello", **fields)
    line = capsys.readouterr().out.strip().splitlines()[-1]
    parsed: dict[str, object] = json.loads(line)
    return parsed


@pytest.mark.parametrize(
    "field",
    [
        "api_key",
        "X-API-Key",
        "bot_secret",
        "webhook_secret",
        "password",
        "base64Data",
        "request_body",
        "otp",
        "message",
        "text",
        "authorization",
    ],
)
def test_sensitive_fields_are_redacted(capsys: pytest.CaptureFixture[str], field: str) -> None:
    out = emit(capsys, **{field: "super-sensitive-value"})
    assert out[field] == REDACTED
    assert "super-sensitive-value" not in json.dumps(out)


def test_non_sensitive_fields_are_kept(capsys: pytest.CaptureFixture[str]) -> None:
    out = emit(capsys, vendor_id="abc", status_code=200, footprint="big")
    assert out["vendor_id"] == "abc"
    assert out["status_code"] == 200
    assert out["footprint"] == "big"


def test_nested_values_are_redacted(capsys: pytest.CaptureFixture[str]) -> None:
    out = emit(capsys, payload={"media": {"base64Data": "AAAA", "mimeType": "image/png"}})
    assert out["payload"] == {"media": {"base64Data": REDACTED, "mimeType": "image/png"}}


def test_key_pattern_is_scrubbed_inside_free_text(capsys: pytest.CaptureFixture[str]) -> None:
    out = emit(capsys, note=f"failed for key {LIVE_KEY} and whsec_abc-DEF_123")
    assert LIVE_KEY not in json.dumps(out)
    assert "whsec_abc" not in json.dumps(out)
    get_logger("t").info(f"event with {LIVE_KEY}")
    assert LIVE_KEY not in capsys.readouterr().out
