import asyncio
import json
import uuid
from collections.abc import Callable

import httpx
import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from app.core.config import AppEnv, Settings
from app.core.signing import verify_signature
from app.schemas.ai_reply import AiReplyRequest, IncomingMessage
from app.services.bot_proxy import BotCallError, call_bot

SECRET = "whsec_unit_test_secret"
VENDOR_ID = uuid.uuid4()
PUBLIC_IP = "93.184.216.34"

Handler = Callable[[httpx.Request], httpx.Response]


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "app_env": AppEnv.TEST,
        "secret_encryption_key": SecretStr(Fernet.generate_key().decode()),
        "allow_private_bot_urls": True,
        "bot_timeout_seconds": 5.0,
        "max_bot_response_bytes": 10_000,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


def payload(text: str = "hello") -> AiReplyRequest:
    return AiReplyRequest(chat_id="1@c.us", incoming_messages=[IncomingMessage(text=text)])


class Bot:
    """A fake network: records every request, answers with ``handler``."""

    def __init__(self, handler: Handler | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self.handler = handler or (lambda _r: httpx.Response(200, json={"replies": ["hi"]}))
        self.transport = httpx.MockTransport(self._handle)

    async def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handler(request)


async def call(
    bot: Bot,
    *,
    settings: Settings | None = None,
    base_url: str = "http://bot.test/ai-reply",
    text: str = "hello",
    resolver_ips: tuple[str, ...] = (PUBLIC_IP,),
) -> list[str]:
    async def resolver(_host: str) -> list[str]:
        return list(resolver_ips)

    result = await call_bot(
        url=base_url,
        secret=SECRET,
        vendor_id=VENDOR_ID,
        request_id="req-123",
        payload=payload(text),
        settings=settings or make_settings(),
        transport=bot.transport,
        resolver=resolver,
    )
    return result.replies


async def error_of(bot: Bot, **kw: object) -> BotCallError:
    with pytest.raises(BotCallError) as info:
        await call(bot, **kw)  # type: ignore[arg-type]
    return info.value


async def test_success_sends_a_correctly_signed_contract_request() -> None:
    bot = Bot()
    assert await call(bot, text="سلام 👋") == ["hi"]

    (request,) = bot.requests
    assert request.method == "POST"
    assert str(request.url) == "http://bot.test/ai-reply"
    raw = request.content
    verify_signature(SECRET, request.headers, raw)  # the real check vendors do
    assert request.headers["x-request-id"] == "req-123"
    assert request.headers["x-vendor-id"] == str(VENDOR_ID)
    assert request.headers["x-contract-version"] == "1"
    assert request.headers["content-type"] == "application/json"
    body = json.loads(raw)
    assert body["contractVersion"] == 1
    assert body["requestId"] == "req-123"
    assert body["incomingMessages"][0]["text"] == "سلام 👋"
    assert "\\u" not in raw.decode()  # sent as real UTF-8, not escaped


async def test_report_contains_headers_and_redacted_request() -> None:
    bot = Bot()
    result = await call_bot(
        url="http://bot.test/ai-reply",
        secret=SECRET,
        vendor_id=VENDOR_ID,
        request_id="r",
        payload=payload(),
        settings=make_settings(),
        transport=bot.transport,
    )
    report = result.report
    assert report.response_status == 200
    assert report.request_headers["X-Gateway-Signature"].startswith("v1=")
    assert report.request["requestId"] == "r"
    assert report.latency_ms >= 0
    assert report.validation_errors == []


@pytest.mark.parametrize("status", [400, 401, 404, 500, 503])
async def test_http_errors_become_502_and_are_not_retried(status: int) -> None:
    bot = Bot(lambda _r: httpx.Response(status, text="nope"))
    error = await error_of(bot)
    assert (error.status_code, error.code) == (502, "BOT_HTTP_ERROR")
    assert str(status) in error.message
    assert error.report.response_status == status
    assert len(bot.requests) == 1


async def test_redirect_is_not_followed() -> None:
    bot = Bot(lambda _r: httpx.Response(302, headers={"Location": "http://169.254.169.254/"}))
    error = await error_of(bot)
    assert error.code == "BOT_REDIRECT_NOT_ALLOWED"
    assert len(bot.requests) == 1
    assert all("169.254" not in str(r.url) for r in bot.requests)


@pytest.mark.parametrize(
    ("response", "fragment"),
    [
        (httpx.Response(200, text="{not json"), "not valid JSON"),
        (httpx.Response(200, json={"replies": "string"}), "must be a list"),
        (httpx.Response(200, json=["a"]), "must be a JSON object"),
        (httpx.Response(200, text=""), "not valid JSON"),
    ],
)
async def test_invalid_answers_are_502_with_the_reason(
    response: httpx.Response, fragment: str
) -> None:
    error = await error_of(Bot(lambda _r: response))
    assert (error.status_code, error.code) == (502, "BOT_INVALID_RESPONSE")
    assert fragment in error.message
    assert fragment in error.report.validation_errors[0]


async def test_empty_replies_are_fine() -> None:
    assert await call(Bot(lambda _r: httpx.Response(200, json={"replies": []}))) == []


async def test_response_over_the_limit_is_refused() -> None:
    bot = Bot(lambda _r: httpx.Response(200, content=b"x" * 20_000))
    assert (await error_of(bot)).code == "BOT_RESPONSE_TOO_LARGE"


async def test_slow_bot_times_out_with_504() -> None:
    async def slow(_request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200, json={"replies": []})

    transport = httpx.MockTransport(slow)
    with pytest.raises(BotCallError) as info:
        await call_bot(
            url="http://bot.test/ai-reply",
            secret=SECRET,
            vendor_id=VENDOR_ID,
            request_id="r",
            payload=payload(),
            settings=make_settings(bot_timeout_seconds=0.2),
            transport=transport,
        )
    assert (info.value.status_code, info.value.code) == (504, "BOT_TIMEOUT")


@pytest.mark.parametrize(
    ("exc", "code", "status"),
    [
        (httpx.ConnectError("boom"), "BOT_UNREACHABLE", 502),
        (httpx.ReadError("boom"), "BOT_UNREACHABLE", 502),
        (httpx.ConnectTimeout("boom"), "BOT_TIMEOUT", 504),
        (httpx.ReadTimeout("boom"), "BOT_TIMEOUT", 504),
    ],
)
async def test_network_failures(exc: Exception, code: str, status: int) -> None:
    def handler(_r: httpx.Request) -> httpx.Response:
        raise exc

    error = await error_of(Bot(handler))
    assert (error.status_code, error.code) == (status, code)


async def test_private_address_is_refused_when_not_allowed() -> None:
    bot = Bot()
    settings = make_settings(allow_private_bot_urls=False)
    error = await error_of(
        bot, settings=settings, base_url="https://bot.test", resolver_ips=("10.0.0.5",)
    )
    assert (error.status_code, error.code) == (502, "BOT_URL_NOT_ALLOWED")
    assert bot.requests == []  # nothing was sent anywhere


async def test_http_is_refused_when_not_allowed() -> None:
    bot = Bot()
    error = await error_of(bot, settings=make_settings(allow_private_bot_urls=False))
    assert error.code == "BOT_URL_NOT_ALLOWED"
    assert bot.requests == []


async def test_public_https_bot_is_called_on_the_vetted_ip_with_hostname_kept() -> None:
    bot = Bot()
    settings = make_settings(allow_private_bot_urls=False)
    await call(bot, settings=settings, base_url="https://bot.vendor.com:8443/v1/ai-reply")

    (request,) = bot.requests
    assert request.url.host == PUBLIC_IP  # we connect to the IP we checked
    assert request.url.port == 8443
    assert request.url.path == "/v1/ai-reply"
    assert request.headers["host"] == "bot.vendor.com:8443"
    assert request.extensions["sni_hostname"] == "bot.vendor.com"
    verify_signature(SECRET, request.headers, request.content)


async def test_next_vetted_ip_is_tried_when_one_is_unreachable() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.host)
        if request.url.host == "93.184.216.34":
            raise httpx.ConnectError("down")
        return httpx.Response(200, json={"replies": ["ok"]})

    bot = Bot(handler)
    settings = make_settings(allow_private_bot_urls=False)
    replies = await call(
        bot,
        settings=settings,
        base_url="https://bot.vendor.com/ai-reply",
        resolver_ips=("93.184.216.34", "93.184.216.35"),
    )
    assert replies == ["ok"]
    assert seen == ["93.184.216.34", "93.184.216.35"]


async def test_http_error_does_not_try_other_ips() -> None:
    bot = Bot(lambda _r: httpx.Response(500))
    settings = make_settings(allow_private_bot_urls=False)
    await error_of(
        bot,
        settings=settings,
        base_url="https://bot.vendor.com/ai-reply",
        resolver_ips=("93.184.216.34", "93.184.216.35"),
    )
    assert len(bot.requests) == 1


async def test_posts_the_saved_url_exactly_and_sends_vendor_context() -> None:
    bot = Bot()
    result = await call_bot(
        url="http://bot.test/clinics/gulberg/whatsapp",
        secret=SECRET,
        vendor_id=VENDOR_ID,
        request_id="req-ctx",
        payload=payload(),
        settings=make_settings(),
        transport=bot.transport,
        vendor_context={"clinicId": "gulberg", "city": "LHR"},
    )
    assert result.replies == ["hi"]
    (request,) = bot.requests
    assert str(request.url) == "http://bot.test/clinics/gulberg/whatsapp"
    body = json.loads(request.content)
    assert body["vendorContext"] == {"clinicId": "gulberg", "city": "LHR"}
    assert list(body)[-1] == "vendorContext"
