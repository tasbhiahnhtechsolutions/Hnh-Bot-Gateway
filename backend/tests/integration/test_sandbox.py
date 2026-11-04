import json
from typing import Any

import httpx
import pytest
from pytest import MonkeyPatch
from sqlalchemy import select

from app.core.signing import verify_signature
from app.models import RequestLog
from app.repositories import request_logs
from app.services.simulate import check_bot_connection
from tests.integration.conftest import REQUEST_BODY, Api, MakeApi

URL = "/v1/sandbox/simulate-incoming"


def headers(key: str) -> dict[str, str]:
    return {"X-API-Key": key, "Content-Type": "application/json"}


async def simulate(api: Api, key: str, body: dict[str, Any] | bytes) -> httpx.Response:
    content = body if isinstance(body, bytes) else json.dumps(body).encode()
    return await api.client.post(URL, content=content, headers=headers(key))


async def logs(api: Api) -> list[RequestLog]:
    async with api.app.state.session_factory() as db:
        return list(await db.scalars(select(RequestLog).order_by(RequestLog.id)))


async def test_test_key_runs_the_vendors_real_bot_and_returns_a_report(make_api: MakeApi) -> None:
    api = await make_api()
    created = await api.vendor(bot_base_url="http://bot.test/ai-reply")
    api.bot.handler = lambda _r: httpx.Response(200, json={"replies": ["Ji hai!"]})

    response = await simulate(api, created.secrets.test_key, {"text": "Red lehenga available?"})

    assert response.status_code == 200
    report = response.json()
    assert report["ok"] is True
    assert report["replies"] == ["Ji hai!"]
    assert report["errorCode"] is None
    assert report["requestId"].startswith("sim-")
    assert report["bot"]["url"] == "http://bot.test/ai-reply"
    assert report["bot"]["responseStatus"] == 200
    assert report["bot"]["request"]["incomingMessages"][0]["text"] == "Red lehenga available?"
    assert "X-Gateway-Signature" in report["bot"]["requestHeaders"]
    assert report["bot"]["validationErrors"] == []

    (sent,) = api.bot.requests
    verify_signature(created.secrets.bot_secret, sent.headers, sent.content)
    assert created.secrets.bot_secret not in response.text
    assert created.secrets.test_key not in response.text


async def test_sandbox_reaches_the_bot_even_when_the_vendor_is_in_canned_mode(
    make_api: MakeApi,
) -> None:
    api = await make_api()
    created = await api.vendor(inbound_mode="canned")
    response = await simulate(api, created.secrets.test_key, {"text": "hi"})
    assert response.json()["mode"] == "forward"
    assert len(api.bot.requests) == 1


async def test_live_key_is_refused_and_the_bot_is_not_called(make_api: MakeApi) -> None:
    api = await make_api()
    created = await api.vendor()
    response = await simulate(api, created.secrets.live_key, {"text": "hi"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "TEST_KEY_REQUIRED"
    assert api.bot.requests == []


async def test_missing_or_wrong_key_is_401(make_api: MakeApi) -> None:
    api = await make_api()
    response = await api.client.post(URL, json={"text": "hi"})
    assert response.status_code == 401
    assert (await simulate(api, "wab_test_00000000_nope", {"text": "hi"})).status_code == 401


@pytest.mark.parametrize(
    ("handler_response", "code"),
    [
        (httpx.Response(500, text="boom"), "BOT_HTTP_ERROR"),
        (httpx.Response(200, text="not json"), "BOT_INVALID_RESPONSE"),
        (httpx.Response(200, json={"reply": "x"}), "BOT_INVALID_RESPONSE"),
        (httpx.Response(302, headers={"Location": "http://x"}), "BOT_REDIRECT_NOT_ALLOWED"),
    ],
)
async def test_a_failing_bot_is_a_200_report_with_the_reason(
    make_api: MakeApi, handler_response: httpx.Response, code: str
) -> None:
    api = await make_api()
    created = await api.vendor()
    api.bot.handler = lambda _r: handler_response

    response = await simulate(api, created.secrets.test_key, {"text": "hi"})

    assert response.status_code == 200
    report = response.json()
    assert report["ok"] is False
    assert report["errorCode"] == code
    assert report["replies"] == []
    assert report["bot"]["responseStatus"] is not None
    if code == "BOT_INVALID_RESPONSE":
        assert report["bot"]["validationErrors"]


async def test_wrong_shape_report_says_what_to_fix(make_api: MakeApi) -> None:
    api = await make_api()
    created = await api.vendor()
    api.bot.handler = lambda _r: httpx.Response(200, json={"replies": "just a string"})
    report = (await simulate(api, created.secrets.test_key, {"text": "hi"})).json()
    assert report["errorCode"] == "BOT_INVALID_RESPONSE"
    assert "replies" in " ".join(report["bot"]["validationErrors"])


async def test_unreachable_bot_url_is_reported_not_raised(make_api: MakeApi) -> None:
    api = await make_api(allow_private_bot_urls=False)
    created = await api.vendor()  # the test helper creates it with private URLs allowed
    response = await simulate(api, created.secrets.test_key, {"text": "hi"})
    assert response.status_code == 200
    assert response.json()["errorCode"] == "BOT_URL_NOT_ALLOWED"


async def test_file_contents_are_redacted_in_the_report(make_api: MakeApi) -> None:
    api = await make_api()
    created = await api.vendor()
    media = {
        "mediaType": "image",
        "mimeType": "image/jpeg",
        "fileName": "a.jpg",
        "fileSize": 9,
        "base64Data": "U0VDUkVULUZJTEUtQllURVM=",
    }
    report = (await simulate(api, created.secrets.test_key, {"text": "see", "media": media})).json()
    assert "U0VDUkVULUZJTEUtQllURVM=" not in json.dumps(report)
    sent_media = report["bot"]["request"]["incomingMessages"][0]["media"]
    assert sent_media["base64Data"].startswith("<redacted")
    # ...but the real request did carry the file.
    assert "U0VDUkVULUZJTEUtQllURVM=" in api.bot.requests[0].content.decode()


@pytest.mark.parametrize(
    "body",
    [
        b"{}",
        b'{"text": "   "}',
        b'{"text": "x", "extra": 1}',
        b"not json",
        b'{"media": {"mediaType": "gif"}}',
    ],
)
async def test_bad_input_is_400_and_never_echoed(make_api: MakeApi, body: bytes) -> None:
    api = await make_api()
    created = await api.vendor()
    response = await simulate(api, created.secrets.test_key, body)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST"
    assert api.bot.requests == []


async def test_too_many_simulations_are_rate_limited(make_api: MakeApi) -> None:
    api = await make_api()
    created = await api.vendor()
    for _ in range(20):
        assert (await simulate(api, created.secrets.test_key, {"text": "hi"})).status_code == 200
    response = await simulate(api, created.secrets.test_key, {"text": "hi"})
    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) >= 1
    assert len(api.bot.requests) == 20


async def test_oversized_body_is_413(make_api: MakeApi) -> None:
    api = await make_api(max_request_body_bytes=2000)
    created = await api.vendor()
    response = await simulate(api, created.secrets.test_key, {"text": "x" * 3000})
    assert response.status_code == 413


# --- request logs ----------------------------------------------------------------------------


async def test_every_plugin_request_leaves_a_metadata_only_log_row(make_api: MakeApi) -> None:
    api = await make_api()
    created = await api.vendor()
    secret_text = "customer-secret-text-987"
    body = {**REQUEST_BODY, "incomingMessages": [{"text": secret_text, "timestamp": "1"}]}
    assert (
        await api.post_reply(created.secrets.live_key, body, {"X-Request-Id": "req-1"})
    ).status_code == 200
    api.bot.handler = lambda _r: httpx.Response(500)
    assert (
        await api.post_reply(created.secrets.live_key, body, {"X-Request-Id": "req-2"})
    ).status_code == 502

    ok_row, error_row = await logs(api)
    assert (ok_row.kind, ok_row.status, ok_row.request_id, ok_row.mode, ok_row.error_code) == (
        "ai_reply",
        "ok",
        "req-1",
        "forward",
        None,
    )
    assert (error_row.status, error_row.error_code, error_row.request_id) == (
        "error",
        "BOT_HTTP_ERROR",
        "req-2",
    )
    assert ok_row.vendor_id == created.vendor.id
    assert ok_row.latency_ms >= 0
    dump = " ".join(str(v) for row in (ok_row, error_row) for v in vars(row).values())
    assert secret_text not in dump


async def test_rejected_requests_without_a_vendor_are_not_logged(make_api: MakeApi) -> None:
    api = await make_api()
    assert (await api.post_reply("wab_live_00000000_nope")).status_code == 401
    assert await logs(api) == []


async def test_sandbox_runs_are_logged_with_kind_sandbox(make_api: MakeApi) -> None:
    api = await make_api()
    created = await api.vendor()
    await simulate(api, created.secrets.test_key, {"text": "hi"})
    (row,) = await logs(api)
    assert (row.kind, row.status, row.mode) == ("sandbox", "ok", "forward")


async def test_a_broken_log_write_does_not_break_the_reply(
    make_api: MakeApi, monkeypatch: MonkeyPatch
) -> None:
    api = await make_api()
    created = await api.vendor()

    async def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("db down")

    monkeypatch.setattr(request_logs, "add", boom)
    response = await api.post_reply(created.secrets.live_key)
    assert response.status_code == 200
    assert response.json() == {"replies": ["bot says hi"]}


# --- connection test -------------------------------------------------------------------------


async def test_connection_test_passes_with_a_working_bot_even_in_canned_mode(
    make_api: MakeApi,
) -> None:
    api = await make_api()
    created = await api.vendor(inbound_mode="canned")
    async with api.app.state.session_factory() as db:
        report = await check_bot_connection(db, api.app.state.inbound_deps, created.vendor)
    assert report.ok is True
    assert report.mode == "forward"
    (sent,) = api.bot.requests
    verify_signature(created.secrets.bot_secret, sent.headers, sent.content)


async def test_connection_test_fails_clearly_when_the_bot_is_down(make_api: MakeApi) -> None:
    api = await make_api()
    created = await api.vendor()
    api.bot.handler = lambda _r: httpx.Response(503)
    async with api.app.state.session_factory() as db:
        report = await check_bot_connection(db, api.app.state.inbound_deps, created.vendor)
    assert (report.ok, report.error_code) == (False, "BOT_HTTP_ERROR")
    assert report.bot is not None
    assert report.bot.response_status == 503
