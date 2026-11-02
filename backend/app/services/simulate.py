"""Run the inbound pipeline without WhatsApp and explain what happened (plan section 7.2)."""

import base64
import mimetypes
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, get_args

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.models import Vendor
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


def build_test_payload(
    *, text: str = "", file: Path | None = None, media_type: str | None = None
) -> AiReplyRequest:
    media: Media | None = None
    if file is not None:
        kind = media_type or guess_media_type(file)
        if kind not in get_args(MediaType):
            raise ApiError(
                400, "INVALID_REQUEST", f"media type must be one of {get_args(MediaType)}"
            )
        data = file.read_bytes()
        too_big = len(data) > LARGE_FILE_LIMIT
        media = Media(
            media_type=kind,
            mime_type=mimetypes.guess_type(file.name)[0] or "application/octet-stream",
            file_name=file.name,
            file_size=len(data),
            caption=text,
            base64_data=None if too_big else base64.b64encode(data).decode("ascii"),
            skipped_large_file=too_big,
        )
    return AiReplyRequest(
        chat_id="923001112222@c.us",
        chat_title="Test Customer",
        sender_name="Test Customer",
        sender_number="+923001112222",
        incoming_messages=[IncomingMessage(text=text, timestamp="12:00:00", media=media)],
    )


async def simulate_incoming(
    db: AsyncSession,
    deps: InboundDeps,
    vendor: Vendor,
    payload: AiReplyRequest,
    *,
    scenario: MockScenario = MockScenario.OK,
) -> SimulationReport:
    """Same code path as a real plugin request. Errors become part of the report."""
    try:
        result = await process_inbound(
            db, deps, vendor, payload, f"sim-{uuid.uuid4()}", scenario=scenario
        )
    except BotCallError as exc:
        return SimulationReport(vendor.inbound_mode, False, [], exc.code, exc.message, exc.report)
    except ApiError as exc:
        return SimulationReport(vendor.inbound_mode, False, [], exc.code, exc.message, None)
    return SimulationReport(vendor.inbound_mode, True, result.replies, None, None, result.report)


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
