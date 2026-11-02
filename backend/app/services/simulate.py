"""Run the inbound pipeline without WhatsApp and explain what happened (plan section 7.2)."""

import base64
import mimetypes
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, get_args

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.models import InboundMode, Vendor
from app.schemas.ai_reply import AiReplyRequest, IncomingMessage, Media, MediaType
from app.services.bot_proxy import BotCallError, BotCallReport
from app.services.inbound import InboundDeps, process_inbound
from app.services.mock_bot import MockScenario

LARGE_FILE_LIMIT = 15 * 1024 * 1024  # ai-contract.md section 4.7


@dataclass(frozen=True, slots=True)
class SimulationReport:
    mode: str
    ok: bool
    replies: list[str]
    error_code: str | None
    error_message: str | None
    bot: BotCallReport | None


def guess_media_type(path: Path) -> MediaType:
    mime = mimetypes.guess_type(path.name)[0] or ""
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("audio/"):
        return "audio"
    return "document"


def build_payload(
    *,
    text: str = "",
    file_name: str | None = None,
    file_bytes: bytes | None = None,
    media_type: str | None = None,
) -> AiReplyRequest:
    """A fake one-message customer request, optionally with one attached file."""
    media: Media | None = None
    if file_bytes is not None:
        name = file_name or "file"
        kind = media_type or guess_media_type(Path(name))
        if kind not in get_args(MediaType):
            raise ApiError(
                400, "INVALID_REQUEST", f"media type must be one of {get_args(MediaType)}"
            )
        too_big = len(file_bytes) > LARGE_FILE_LIMIT
        media = Media(
            media_type=kind,
            mime_type=mimetypes.guess_type(name)[0] or "application/octet-stream",
            file_name=name,
            file_size=len(file_bytes),
            caption=text,
            base64_data=None if too_big else base64.b64encode(file_bytes).decode("ascii"),
            skipped_large_file=too_big,
        )
    return AiReplyRequest(
        chat_id="923001112222@c.us",
        chat_title="Test Customer",
        sender_name="Test Customer",
        sender_number="+923001112222",
        incoming_messages=[IncomingMessage(text=text, timestamp="12:00:00", media=media)],
    )


def build_test_payload(
    *, text: str = "", file: Path | None = None, media_type: str | None = None
) -> AiReplyRequest:
    return build_payload(
        text=text,
        file_name=file.name if file else None,
        file_bytes=file.read_bytes() if file else None,
        media_type=media_type,
    )


async def simulate_incoming(
    db: AsyncSession,
    deps: InboundDeps,
    vendor: Vendor,
    payload: AiReplyRequest,
    *,
    scenario: MockScenario = MockScenario.OK,
    mode_override: InboundMode | None = None,
    request_id: str | None = None,
) -> SimulationReport:
    """Same code path as a real plugin request. Errors become part of the report."""
    mode = mode_override.value if mode_override else vendor.inbound_mode
    try:
        result = await process_inbound(
            db,
            deps,
            vendor,
            payload,
            request_id or f"sim-{uuid.uuid4()}",
            scenario=scenario,
            mode_override=mode_override,
        )
    except BotCallError as exc:
        return SimulationReport(mode, False, [], exc.code, exc.message, exc.report)
    except ApiError as exc:
        return SimulationReport(mode, False, [], exc.code, exc.message, None)
    return SimulationReport(mode, True, result.replies, None, None, result.report)


CONNECTION_TEST_TEXT = "Connection test from the gateway. Please answer with any reply."


async def check_bot_connection(
    db: AsyncSession, deps: InboundDeps, vendor: Vendor, *, request_id: str | None = None
) -> SimulationReport:
    """Send one signed sample request to the vendor's real bot (plan sections 7.2, 9.1)."""
    return await simulate_incoming(
        db,
        deps,
        vendor,
        build_test_payload(text=CONNECTION_TEST_TEXT),
        mode_override=InboundMode.FORWARD,
        request_id=request_id,
    )


def report_to_dict(report: SimulationReport) -> dict[str, Any]:
    bot: dict[str, Any] | None = None
    if report.bot is not None:
        b = report.bot
        bot = {
            "url": b.url,
            "requestHeaders": b.request_headers,
            "request": b.request,
            "requestBytes": b.request_bytes,
            "responseStatus": b.response_status,
            "responseExcerpt": b.response_excerpt,
            "latencyMs": b.latency_ms,
            "validationErrors": b.validation_errors,
        }
    return {
        "mode": report.mode,
        "ok": report.ok,
        "replies": report.replies,
        "errorCode": report.error_code,
        "errorMessage": report.error_message,
        "bot": bot,
    }
