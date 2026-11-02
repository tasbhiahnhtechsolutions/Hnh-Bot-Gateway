"""Call the vendor bot: sign, SSRF-safe connect, timeout, size limit, validate (plan 6, 6.1)."""

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.core.config import Settings
from app.core.errors import ApiError
from app.core.signing import signature_header
from app.schemas.ai_reply import (
    CONTRACT_VERSION,
    AiReplyRequest,
    bot_request_body,
    redacted_summary,
)
from app.services.bot_url import Resolver, resolve_host, validate_bot_url
from app.services.replies import parse_bot_response

EXCERPT_CHARS = 2000


@dataclass(slots=True)
class BotCallReport:
    """What happened during one bot call. Safe to show: file contents are redacted."""

    url: str
    request_headers: dict[str, str]
    request: dict[str, Any]
    request_bytes: int
    response_status: int | None = None
    response_excerpt: str | None = None
    latency_ms: int = 0
    validation_errors: list[str] = field(default_factory=list)
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class BotCallResult:
    replies: list[str]
    report: BotCallReport


class BotCallError(ApiError):
    """The bot call failed. ``report`` says how far it got."""

    def __init__(self, status_code: int, code: str, message: str, report: BotCallReport) -> None:
        super().__init__(status_code, code, message)
        report.error_code = code
        self.report = report


async def call_bot(
    *,
    base_url: str,
    secret: str,
    vendor_id: uuid.UUID,
    request_id: str,
    payload: AiReplyRequest,
    settings: Settings,
    transport: httpx.AsyncBaseTransport | None = None,
    trusted: bool = False,
    resolver: Resolver = resolve_host,
) -> BotCallResult:
    """POST the signed request to ``{base_url}/ai-reply`` and return the validated replies.

    ``trusted`` is only for our own in-process mock bot: it skips the SSRF check. We never
    retry (a retry could make the customer get two answers) and never follow redirects.
    """
    target = base_url.rstrip("/") + "/ai-reply"
    body_dict = bot_request_body(payload, request_id)
    raw = json.dumps(body_dict, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    timestamp = int(time.time())
    headers = {
        "Content-Type": "application/json",
        "X-Gateway-Timestamp": str(timestamp),
        "X-Gateway-Signature": signature_header([secret], timestamp, raw),
        "X-Request-Id": request_id,
        "X-Vendor-Id": str(vendor_id),
        "X-Contract-Version": str(CONTRACT_VERSION),
        "User-Agent": "vendor-gateway/1",
    }
    report = BotCallReport(
        url=target,
        request_headers={k: v for k, v in headers.items() if k != "User-Agent"},
        request=redacted_summary(body_dict),
        request_bytes=len(raw),
    )
    started = time.monotonic()

    try:
        ips: list[str] = []
        if not trusted:
            try:
                ips = await validate_bot_url(
                    target, allow_private=settings.allow_private_bot_urls, resolver=resolver
                )
            except ApiError as exc:
                raise BotCallError(502, "BOT_URL_NOT_ALLOWED", exc.message, report) from exc

        try:
            async with asyncio.timeout(settings.bot_timeout_seconds):
                status, response_body = await post_pinned(
                    target,
                    headers,
                    raw,
                    ips,
                    transport,
                    timeout_seconds=settings.bot_timeout_seconds,
                    max_bytes=settings.max_bot_response_bytes,
                )
        except TimeoutError as exc:
            raise BotCallError(
                504,
                "BOT_TIMEOUT",
                f"bot did not answer in {settings.bot_timeout_seconds:g}s",
                report,
            ) from exc
        except httpx.TimeoutException as exc:
            raise BotCallError(504, "BOT_TIMEOUT", "bot connection timed out", report) from exc
        except TooLargeError as exc:
            raise BotCallError(
                502, "BOT_RESPONSE_TOO_LARGE", "bot response is too large", report
            ) from exc
        except httpx.HTTPError as exc:
            raise BotCallError(
                502, "BOT_UNREACHABLE", f"could not reach the bot ({type(exc).__name__})", report
            ) from exc

        report.response_status = status
        report.response_excerpt = response_body[:EXCERPT_CHARS].decode("utf-8", errors="replace")
        if 300 <= status < 400:
            raise BotCallError(
                502,
                "BOT_REDIRECT_NOT_ALLOWED",
                f"bot answered HTTP {status}; redirects are not followed",
                report,
            )
        if not 200 <= status < 300:
            raise BotCallError(502, "BOT_HTTP_ERROR", f"bot answered HTTP {status}", report)

        replies, errors = parse_bot_response(response_body)
        if replies is None:
            report.validation_errors = errors
            raise BotCallError(502, "BOT_INVALID_RESPONSE", errors[0], report)
        return BotCallResult(replies=replies, report=report)
    finally:
        report.latency_ms = round((time.monotonic() - started) * 1000)


class TooLargeError(Exception):
    pass


async def post_pinned(
    url: str,
    headers: dict[str, str],
    raw: bytes,
    ips: list[str],
    transport: httpx.AsyncBaseTransport | None,
    *,
    timeout_seconds: float,
    max_bytes: int,
) -> tuple[int, bytes]:
    """Send once. With vetted IPs we connect to the IP itself and keep the hostname for the
    Host header and TLS, so the answer of a second DNS lookup cannot redirect us (DNS rebinding).
    """
    parsed = httpx.URL(url)
    async with httpx.AsyncClient(
        transport=transport, follow_redirects=False, timeout=timeout_seconds
    ) as client:
        if not ips:
            return await _send(client, url, headers, raw, {}, max_bytes)

        last_error: httpx.HTTPError | None = None
        for ip in ips:
            pinned = parsed.copy_with(host=ip)
            pin_headers = {**headers, "Host": parsed.netloc.decode("ascii")}
            extensions = {"sni_hostname": parsed.host}
            try:
                return await _send(client, str(pinned), pin_headers, raw, extensions, max_bytes)
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                last_error = exc  # this address is unreachable: try the next vetted one
        assert last_error is not None
        raise last_error


async def _send(
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    raw: bytes,
    extensions: dict[str, Any],
    max_bytes: int,
) -> tuple[int, bytes]:
    request = client.build_request("POST", url, headers=headers, content=raw, extensions=extensions)
    response = await client.send(request, stream=True)
    try:
        chunks: list[bytes] = []
        size = 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > max_bytes:  # counted after decompression
                raise TooLargeError
            chunks.append(chunk)
        return response.status_code, b"".join(chunks)
    finally:
        await response.aclose()
