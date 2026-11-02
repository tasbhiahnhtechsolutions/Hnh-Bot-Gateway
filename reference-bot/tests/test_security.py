import json
import time

import httpx
import pytest

from app.security import SignatureError, compute_signature, verify_signature
from tests.conftest import SECRET, Gateway, request_body, sign


async def test_valid_signature_is_accepted(gateway: Gateway) -> None:
    response = await gateway.post(request_body())
    assert response.status_code == 200
    assert isinstance(response.json()["replies"], list)


async def test_wrong_secret_is_rejected_with_an_empty_401(gateway: Gateway) -> None:
    response = await gateway.post(request_body(), secret="whsec_somebody_else")
    assert response.status_code == 401
    assert response.content == b""


async def test_tampered_body_is_rejected(gateway: Gateway) -> None:
    raw = json.dumps(request_body()).encode()
    ts = int(time.time())
    signature = sign(SECRET, ts, raw)
    tampered = raw.replace(b"hello", b"HELLO")
    response = await gateway.client.post(
        "/ai-reply",
        content=tampered,
        headers={"X-Gateway-Timestamp": str(ts), "X-Gateway-Signature": signature},
    )
    assert response.status_code == 401


async def test_signature_must_cover_the_raw_bytes_not_the_parsed_json(gateway: Gateway) -> None:
    body = request_body()
    raw = json.dumps(body, indent=2).encode()  # same data, different bytes
    ts = int(time.time())
    signed_compact = sign(SECRET, ts, json.dumps(body, separators=(",", ":")).encode())
    response = await gateway.client.post(
        "/ai-reply",
        content=raw,
        headers={"X-Gateway-Timestamp": str(ts), "X-Gateway-Signature": signed_compact},
    )
    assert response.status_code == 401


@pytest.mark.parametrize("offset", [-301, 301, -3600, 86400])
async def test_old_or_future_timestamps_are_rejected(gateway: Gateway, offset: int) -> None:
    response = await gateway.post(request_body(), timestamp=int(time.time()) + offset)
    assert response.status_code == 401


@pytest.mark.parametrize("offset", [-290, 290])
async def test_timestamps_inside_the_window_are_accepted(gateway: Gateway, offset: int) -> None:
    response = await gateway.post(request_body(), timestamp=int(time.time()) + offset)
    assert response.status_code == 200


async def test_missing_headers_are_rejected(gateway: Gateway) -> None:
    raw = json.dumps(request_body()).encode()
    for headers in (
        {},
        {"X-Gateway-Timestamp": str(int(time.time()))},
        {"X-Gateway-Signature": "v1=abc"},
        {"X-Gateway-Timestamp": "not-a-number", "X-Gateway-Signature": "v1=abc"},
    ):
        response = await gateway.client.post("/ai-reply", content=raw, headers=headers)
        assert response.status_code == 401, headers


async def test_rotation_header_with_two_signatures_works_with_either_secret(
    gateway: Gateway,
) -> None:
    raw = json.dumps(request_body()).encode()
    ts = int(time.time())
    for pair in (
        f"{sign(SECRET, ts, raw)},{sign('whsec_old', ts, raw)}",
        f"{sign('whsec_new', ts, raw)},{sign(SECRET, ts, raw)}",
    ):
        response = await gateway.client.post(
            "/ai-reply",
            content=raw,
            headers={"X-Gateway-Timestamp": str(ts), "X-Gateway-Signature": pair},
        )
        assert response.status_code == 200
    neither = await gateway.client.post(
        "/ai-reply",
        content=raw,
        headers={
            "X-Gateway-Timestamp": str(ts),
            "X-Gateway-Signature": f"{sign('whsec_a', ts, raw)},{sign('whsec_b', ts, raw)}",
        },
    )
    assert neither.status_code == 401


async def test_unsigned_requests_never_reach_the_handler(gateway: Gateway) -> None:
    # Even garbage must get 401 (not 400): we do not look at the body before the signature.
    response = await gateway.client.post("/ai-reply", content=b"{not json")
    assert response.status_code == 401


def test_verify_signature_unit_cases() -> None:
    raw = b'{"a":1}'
    headers = {"X-Gateway-Timestamp": "1000", "X-Gateway-Signature": sign(SECRET, 1000, raw)}
    verify_signature(SECRET, headers, raw, now=1000)
    verify_signature(SECRET, headers, raw, now=1300)
    with pytest.raises(SignatureError, match="stale"):
        verify_signature(SECRET, headers, raw, now=1301)
    with pytest.raises(SignatureError, match="bad signature"):
        verify_signature("whsec_other", headers, raw, now=1000)
    with pytest.raises(SignatureError, match="stale"):
        verify_signature(SECRET, headers, raw, tolerance=10, now=1100)


def test_signature_matches_a_known_vector() -> None:
    # Fixed vector, checked with `openssl dgst -sha256 -hmac` (reused by the Node check, step 7).
    # HMAC_SHA256("whsec_test", "1730000000." + '{"hello":"world"}')
    assert (
        compute_signature("whsec_test", 1730000000, b'{"hello":"world"}')
        == "7e4655cdf521de03e796dae58d28aea6d4808347f20a58ea7eddc496a701768b"
    )


async def test_health_needs_no_signature(gateway: Gateway) -> None:
    response: httpx.Response = await gateway.client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
