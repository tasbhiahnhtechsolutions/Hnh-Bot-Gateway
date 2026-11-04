"""The inbound pipeline: one plugin request -> replies (plan section 6, modes in 7.1)."""

import uuid
from dataclasses import dataclass

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ApiError
from app.core.security import SecretBox
from app.models import InboundMode, Vendor
from app.repositories import canned_rules
from app.schemas.ai_reply import AiReplyRequest
from app.services.bot_proxy import BotCallReport, call_bot
from app.services.bot_url import Resolver, resolve_host
from app.services.canned import pick_canned_reply
from app.services.echo import make_echo_replies
from app.services.mock_bot import MockScenario, create_mock_bot_app

MOCK_BOT_URL = "http://mock-bot.internal/ai-reply"


@dataclass(frozen=True, slots=True)
class InboundDeps:
    settings: Settings
    box: SecretBox
    bot_transport: httpx.AsyncBaseTransport | None = None  # tests inject a fake network here
    resolver: Resolver = resolve_host


@dataclass(frozen=True, slots=True)
class InboundResult:
    replies: list[str]
    mode: str
    report: BotCallReport | None  # only modes that call a bot have one


async def process_inbound(
    db: AsyncSession,
    deps: InboundDeps,
    vendor: Vendor,
    payload: AiReplyRequest,
    request_id: str,
    *,
    scenario: MockScenario = MockScenario.OK,
    mode_override: InboundMode | None = None,
) -> InboundResult:
    # ``mode_override`` lets the sandbox and the connection test reach the real bot even when the
    # vendor is switched to canned/echo/paused.
    mode = mode_override.value if mode_override else vendor.inbound_mode
    if mode == InboundMode.PAUSED:
        return InboundResult([], mode, None)
    if mode == InboundMode.CANNED:
        rules = await canned_rules.list_for_vendor(db, vendor.id)
        return InboundResult(pick_canned_reply(rules, payload), mode, None)
    if mode == InboundMode.ECHO:
        return InboundResult(make_echo_replies(payload), mode, None)

    secret = deps.box.decrypt(vendor.bot_secret_enc)
    if mode == InboundMode.MOCK_BOT:
        if not deps.settings.enable_mock_bot:
            raise ApiError(503, "MOCK_BOT_DISABLED", "mock bot is not enabled in this environment")
        mock = create_mock_bot_app(
            secret, scenario, slow_seconds=deps.settings.mock_bot_slow_seconds
        )
        result = await call_bot(
            url=MOCK_BOT_URL,
            secret=secret,
            vendor_id=vendor.id,
            request_id=request_id,
            payload=payload,
            settings=deps.settings,
            transport=httpx.ASGITransport(app=mock),
            trusted=True,
            vendor_context=vendor.vendor_context or {},
        )
        return InboundResult(result.replies, mode, result.report)

    if mode != InboundMode.FORWARD:  # the database check constraint makes this unreachable
        raise ApiError(500, "INTERNAL_ERROR", "unknown inbound mode")
    if not vendor.bot_base_url:
        raise ApiError(503, "BOT_NOT_CONFIGURED", "this vendor has no bot URL")
    result = await call_bot(
        url=vendor.bot_base_url,
        secret=secret,
        vendor_id=vendor.id,
        request_id=request_id,
        payload=payload,
        settings=deps.settings,
        transport=deps.bot_transport,
        resolver=deps.resolver,
        vendor_context=vendor.vendor_context or {},
    )
    return InboundResult(result.replies, mode, result.report)


def new_request_id() -> str:
    return str(uuid.uuid4())
