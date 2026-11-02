import asyncio
import json
from typing import Any

import httpx
import pytest

from app.core.signing import verify_signature
from app.models import CannedMatchType, CannedRule
from app.repositories import canned_rules
from tests.integration.conftest import REQUEST_BODY, MakeApi

SECRET_TEXT = "my-secret-customer-text-123"


def with_text(text: str) -> dict[str, Any]:
    return {**REQUEST_BODY, "incomingMessages": [{"text": text, "timestamp": "19:30:12"}]}


def error_code(response: httpx.Response) -> str:
    code: str = response.json()["error"]["code"]
    return code


# --- modes -----------------------------------------------------------------------------------


async def test_forward_sends_a_signed_contract_request_and_returns_the_replies(
    make_api: MakeApi,
) -> None:
    api = await make_api()
    created = await api.vendor(bot_base_url="http://bot.test")
    api.bot.handler = lambda _r: httpx.Response(
        200, json={"replies": ["Ji, available hai.", "Price 85,000"]}
    )

    response = await api.post_reply(
        created.secrets.live_key,
        with_text("Red lehenga available hai? سلام 👋"),
        headers={"X-Request-Id": "req-abc", "X-WA-Number": created.vendor.phone_e164},
    )

    assert response.status_code == 200
    assert response.json() == {"replies": ["Ji, available hai.", "Price 85,000"]}

    (sent,) = api.bot.requests
    assert str(sent.url) == "http://bot.test/ai-reply"
    verify_signature(created.secrets.bot_secret, sent.headers, sent.content)
    assert sent.headers["x-request-id"] == "req-abc"
    assert sent.headers["x-vendor-id"] == str(created.vendor.id)
    body = json.loads(sent.content)
    assert body["contractVersion"] == 1
    assert body["requestId"] == "req-abc"
    assert body["incomingMessages"][0]["text"] == "Red lehenga available hai? سلام 👋"
    assert body["senderNumber"] == "+923001112222"


async def test_plugin_only_fields_are_stripped_and_media_is_forwarded(make_api: MakeApi) -> None:
    api = await make_api()
    created = await api.vendor()
    media = {
        "hasMedia": True,
        "mediaType": "image",
        "mimeType": "image/jpeg",
        "fileName": "i",
        "fileSize": 3,
        "caption": "c",
        "base64Data": "QUJD",
        "previewUrl": "blob:x",
        "thumbnailBase64": "THUMB",
    }
    body = {**REQUEST_BODY, "incomingMessages": [{"text": "c", "timestamp": "1", "media": media}]}
    assert (await api.post_reply(created.secrets.live_key, body)).status_code == 200

    sent = json.loads(api.bot.requests[0].content)
    assert sent["incomingMessages"][0]["media"]["base64Data"] == "QUJD"
    assert "previewUrl" not in api.bot.requests[0].content.decode()
    assert "THUMB" not in api.bot.requests[0].content.decode()


async def test_paused_returns_nothing_and_calls_nobody(make_api: MakeApi) -> None:
    api = await make_api()
    created = await api.vendor(inbound_mode="paused")
    response = await api.post_reply(created.secrets.live_key)
    assert (response.status_code, response.json()) == (200, {"replies": []})
    assert api.bot.requests == []


async def test_echo_mode(make_api: MakeApi) -> None:
    api = await make_api()
    created = await api.vendor(inbound_mode="echo")
    response = await api.post_reply(created.secrets.live_key, with_text("hello"))
    assert response.json() == {"replies": ["Aap ne likha: hello"]}
    assert api.bot.requests == []


async def test_canned_mode_uses_rules_and_makes_zero_bot_calls(make_api: MakeApi) -> None:
    api = await make_api()
    created = await api.vendor(inbound_mode="canned")
    async with api.app.state.session_factory() as db:
        for priority, kind, value, reply in [
            (10, CannedMatchType.TEXT_CONTAINS, "price", "Price 85,000 se shuru hota hai."),
            (1000, CannedMatchType.DEFAULT, "", "Shukriya!"),
        ]:
            await canned_rules.add(
                db,
                CannedRule(
                    vendor_id=created.vendor.id,
                    priority=priority,
                    match_type=kind.value,
                    match_value=value,
                    reply_text=reply,
                ),
            )
        await db.commit()

    key = created.secrets.live_key
    assert (await api.post_reply(key, with_text("what is the PRICE?"))).json() == {
        "replies": ["Price 85,000 se shuru hota hai."]
    }
    assert (await api.post_reply(key, with_text("hello"))).json() == {"replies": ["Shukriya!"]}
    assert api.bot.requests == []  # the vendor's bot was never contacted


async def test_canned_mode_without_rules_still_answers(make_api: MakeApi) -> None:
    api = await make_api()
    created = await api.vendor(inbound_mode="canned")
    response = await api.post_reply(created.secrets.live_key)
    assert response.json() == {"replies": ["Shukriya! Hum jald jawab denge."]}


async def test_mock_bot_mode_ok_and_disabled(make_api: MakeApi) -> None:
    api = await make_api()
    created = await api.vendor(inbound_mode="mock_bot")
    response = await api.post_reply(created.secrets.live_key)
    assert response.json() == {"replies": ["Mock bot: received 1 message(s)."]}
    assert api.bot.requests == []

    off = await make_api(enable_mock_bot=False)
    other = await off.vendor(inbound_mode="mock_bot")
    response = await off.post_reply(other.secrets.live_key)
    assert (response.status_code, error_code(response)) == (503, "MOCK_BOT_DISABLED")


# --- bot failures: customer gets nothing, plugin gets 502/504 --------------------------------


@pytest.mark.parametrize(
    ("handler_response", "status", "code"),
    [
        (httpx.Response(500, text="boom"), 502, "BOT_HTTP_ERROR"),
        (httpx.Response(401), 502, "BOT_HTTP_ERROR"),
        (httpx.Response(200, text="{broken"), 502, "BOT_INVALID_RESPONSE"),
        (httpx.Response(200, json={"replies": "text"}), 502, "BOT_INVALID_RESPONSE"),
        (httpx.Response(200, json={"reply": ["x"]}), 502, "BOT_INVALID_RESPONSE"),
        (
            httpx.Response(302, headers={"Location": "http://169.254.169.254/"}),
            502,
            "BOT_REDIRECT_NOT_ALLOWED",
        ),
    ],
)
async def test_bad_bot_answers(
    make_api: MakeApi, handler_response: httpx.Response, status: int, code: str
) -> None:
    api = await make_api()
    created = await api.vendor()
    api.bot.handler = lambda _r: handler_response
    response = await api.post_reply(created.secrets.live_key)
    assert (response.status_code, error_code(response)) == (status, code)
    assert "replies" not in response.json()
    assert len(api.bot.requests) == 1  # never retried, never redirected


async def test_slow_bot_gives_504(make_api: MakeApi) -> None:
    api = await make_api(bot_timeout_seconds=0.3)
    created = await api.vendor()

    async def slow(_r: httpx.Request) -> httpx.Response:
        await asyncio.sleep(3)
        return httpx.Response(200, json={"replies": ["late"]})

    api.bot.transport = httpx.MockTransport(slow)
    api.app.state.inbound_deps = type(api.app.state.inbound_deps)(
        settings=api.settings, box=api.box, bot_transport=api.bot.transport
    )
    response = await api.post_reply(created.secrets.live_key)
    assert (response.status_code, error_code(response)) == (504, "BOT_TIMEOUT")


async def test_bot_down_gives_502(make_api: MakeApi) -> None:
    api = await make_api()
    created = await api.vendor()

    def down(_r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    api.bot.handler = down
    response = await api.post_reply(created.secrets.live_key)
    assert (response.status_code, error_code(response)) == (502, "BOT_UNREACHABLE")


async def test_empty_and_oversized_replies_are_handled_like_the_contract_says(
    make_api: MakeApi,
) -> None:
    api = await make_api()
    created = await api.vendor()
    key = created.secrets.live_key

    api.bot.handler = lambda _r: httpx.Response(200, json={"replies": []})
    assert (await api.post_reply(key)).json() == {"replies": []}

    api.bot.handler = lambda _r: httpx.Response(
        200, json={"replies": [f"m{i}" for i in range(6)], "debug": "ignored"}
    )
    assert (await api.post_reply(key)).json() == {"replies": ["m0", "m1", "m2", "m3", "m4"]}

    api.bot.handler = lambda _r: httpx.Response(200, json={"replies": ["a" * 5000]})
    (bubble,) = (await api.post_reply(key)).json()["replies"]
    assert len(bubble) == 4000


async def test_forward_without_a_bot_url_is_a_clear_error(make_api: MakeApi) -> None:
    api = await make_api()
    created = await api.vendor()
    from app.repositories import vendors

    async with api.app.state.session_factory() as db:
        vendor = await vendors.get(db, created.vendor.id)
        assert vendor is not None
        vendor.bot_base_url = None
        await db.commit()
    response = await api.post_reply(created.secrets.live_key)
    assert (response.status_code, error_code(response)) == (503, "BOT_NOT_CONFIGURED")


async def test_private_bot_url_is_refused_when_the_environment_forbids_it(
    make_api: MakeApi,
) -> None:
    api = await make_api(allow_private_bot_urls=False)
    created_ok = await api.vendor(bot_base_url="http://localhost:9000")  # created in "dev" mode
    # The vendor row exists, but at call time the strict environment must refuse localhost.
    response = await api.post_reply(created_ok.secrets.live_key)
    assert (response.status_code, error_code(response)) == (502, "BOT_URL_NOT_ALLOWED")
    assert api.bot.requests == []


# --- request checks --------------------------------------------------------------------------


async def test_ai_reply_requires_a_valid_key(make_api: MakeApi) -> None:
    api = await make_api()
    for key in ["", "wab_live_00000000_x"]:
        response = await api.post_reply(key)
        assert (response.status_code, error_code(response)) == (401, "INVALID_KEY")
    assert api.bot.requests == []


async def test_invalid_body_gives_400_and_never_echoes_the_input(make_api: MakeApi) -> None:
    api = await make_api()
    created = await api.vendor()
    bad = {**REQUEST_BODY, "incomingMessages": SECRET_TEXT, "chatId": SECRET_TEXT}
    bodies: list[dict[str, Any] | bytes] = [
        bad,
        b"not json " + SECRET_TEXT.encode(),
        {**REQUEST_BODY, "incomingMessages": []},
    ]
    for body in bodies:
        response = await api.post_reply(created.secrets.live_key, body)
        assert (response.status_code, error_code(response)) == (400, "INVALID_REQUEST")
        assert SECRET_TEXT not in response.text
    assert api.bot.requests == []


async def test_video_is_not_a_supported_media_type(make_api: MakeApi) -> None:
    api = await make_api()
    created = await api.vendor()
    media = {"mediaType": "video", "base64Data": "AAAA"}
    body = {**REQUEST_BODY, "incomingMessages": [{"text": "", "media": media}]}
    response = await api.post_reply(created.secrets.live_key, body)
    assert (response.status_code, error_code(response)) == (400, "INVALID_REQUEST")


async def test_oversized_body_gets_413_by_content_length_and_when_chunked(
    make_api: MakeApi,
) -> None:
    api = await make_api(max_request_body_bytes=2000)
    created = await api.vendor()
    big = json.dumps(with_text("x" * 5000)).encode()

    response = await api.post_reply(created.secrets.live_key, big)
    assert (response.status_code, error_code(response)) == (413, "REQUEST_TOO_LARGE")

    async def chunks() -> Any:
        for i in range(0, len(big), 500):
            yield big[i : i + 500]

    chunked = await api.client.post(
        "/v1/ai-reply",
        content=chunks(),
        headers={"X-API-Key": created.secrets.live_key, "Content-Type": "application/json"},
    )
    assert (chunked.status_code, error_code(chunked)) == (413, "REQUEST_TOO_LARGE")
    assert api.bot.requests == []


async def test_a_body_just_under_the_limit_is_accepted(make_api: MakeApi) -> None:
    api = await make_api(max_request_body_bytes=2000)
    created = await api.vendor()
    small = json.dumps(with_text("x" * 50)).encode()
    assert len(small) < 2000
    assert (await api.post_reply(created.secrets.live_key, small)).status_code == 200


async def test_contract_version_header(make_api: MakeApi) -> None:
    api = await make_api()
    created = await api.vendor()
    ok = await api.post_reply(created.secrets.live_key, headers={"X-Contract-Version": "1"})
    assert ok.status_code == 200
    bad = await api.post_reply(created.secrets.live_key, headers={"X-Contract-Version": "2"})
    assert (bad.status_code, error_code(bad)) == (400, "UNSUPPORTED_CONTRACT_VERSION")


async def test_request_id_is_passed_on_or_generated_and_junk_is_replaced(make_api: MakeApi) -> None:
    api = await make_api()
    key = (await api.vendor()).secrets.live_key
    await api.post_reply(key, headers={"X-Request-Id": "good-id_1.2"})
    await api.post_reply(key)
    await api.post_reply(key, headers={"X-Request-Id": "bad id with spaces\"'"})
    ids = [r.headers["x-request-id"] for r in api.bot.requests]
    assert ids[0] == "good-id_1.2"
    assert len(ids[1]) == 36
    assert len(ids[2]) == 36
    assert len(set(ids)) == 3


async def test_wa_number_policy_is_strict_by_default(make_api: MakeApi) -> None:
    strict = await make_api()  # no override: this is what production runs
    assert strict.settings.wa_number_mismatch_policy == "reject"
    mine = await strict.vendor(whatsapp_number="923007654321")
    bad = await strict.post_reply(mine.secrets.live_key, headers={"X-WA-Number": "923009999999"})
    assert (bad.status_code, error_code(bad)) == (403, "NUMBER_MISMATCH")
    missing = await strict.post_reply(mine.secrets.live_key, send_wa_number=False)
    assert (missing.status_code, error_code(missing)) == (403, "NUMBER_MISMATCH")
    assert strict.bot.requests == []  # a rejected request never reaches the vendor's bot
    good = await strict.post_reply(
        mine.secrets.live_key, headers={"X-WA-Number": "+92 300 7654321"}
    )
    assert good.status_code == 200
    assert strict.bot.requests[0].headers["x-vendor-id"] == str(mine.vendor.id)


async def test_wa_number_policy_warn_still_works_when_chosen(make_api: MakeApi) -> None:
    lenient = await make_api(wa_number_mismatch_policy="warn")
    created = await lenient.vendor(whatsapp_number="923001234567")
    mismatch = await lenient.post_reply(
        created.secrets.live_key, headers={"X-WA-Number": "923009999999"}
    )
    assert mismatch.status_code == 200  # only a warning is logged


async def test_each_vendor_is_called_with_its_own_secret_and_url(make_api: MakeApi) -> None:
    api = await make_api()
    one = await api.vendor(bot_base_url="http://one.test")
    two = await api.vendor(bot_base_url="http://two.test")
    await api.post_reply(one.secrets.live_key)
    await api.post_reply(two.secrets.live_key)

    first, second = api.bot.requests
    assert first.url.host == "one.test"
    assert second.url.host == "two.test"
    verify_signature(one.secrets.bot_secret, first.headers, first.content)
    verify_signature(two.secrets.bot_secret, second.headers, second.content)
    with pytest.raises(Exception, match="bad signature"):
        verify_signature(two.secrets.bot_secret, first.headers, first.content)


# --- logging ---------------------------------------------------------------------------------


async def test_logs_contain_no_message_text_files_replies_or_keys(
    make_api: MakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    api = await make_api()
    created = await api.vendor()
    api.bot.handler = lambda _r: httpx.Response(200, json={"replies": ["REPLY-SECRET-456"]})
    media = {"mediaType": "image", "base64Data": "QkFTRTY0LUZJTEU=", "fileName": "f"}
    body = {**REQUEST_BODY, "incomingMessages": [{"text": SECRET_TEXT, "media": media}]}

    await api.post_reply(created.secrets.live_key, body)
    await api.post_reply("wab_live_00000000_wrongkey")
    await api.post_reply(created.secrets.live_key, b"garbage " + SECRET_TEXT.encode())

    logs = capsys.readouterr().out
    assert "ai_reply" in logs
    for secret in [
        SECRET_TEXT,
        "QkFTRTY0LUZJTEU=",
        "REPLY-SECRET-456",
        created.secrets.live_key,
        created.secrets.bot_secret,
        "wrongkey",
    ]:
        assert secret not in logs, secret
    ai_reply_logs = [json.loads(line) for line in logs.splitlines() if '"ai_reply"' in line]
    assert {"request_id", "vendor_id", "latency_ms", "mode"} <= set(ai_reply_logs[0])
