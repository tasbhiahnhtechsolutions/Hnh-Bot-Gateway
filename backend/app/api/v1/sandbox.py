"""Sandbox for vendor developers (plan sections 7.2 and 17): test key only, no WhatsApp."""

import time
import uuid
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic.alias_generators import to_camel

from app.api.body import read_body_limited
from app.api.deps import Auth, Db, Deps
from app.core.errors import ApiError
from app.models import InboundMode, RequestKind
from app.schemas.ai_reply import AiReplyRequest, IncomingMessage, Media
from app.services import rate_limit
from app.services.request_log import record_request
from app.services.simulate import report_to_dict, simulate_incoming

router = APIRouter(prefix="/v1/sandbox", tags=["sandbox"])

SANDBOX_LIMIT_PER_MINUTE = 20


class SimulateRequest(BaseModel):
    """``{"text": "..."}`` is enough. ``media`` follows the contract's media object."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

    text: str = Field(default="", max_length=4000)
    media: Media | None = None


def _parse(raw: bytes) -> SimulateRequest:
    try:
        request = SimulateRequest.model_validate_json(raw)
    except ValidationError as exc:
        problems = [
            f"{'.'.join(str(p) for p in e['loc']) or 'body'}: {e['msg']}" for e in exc.errors()[:5]
        ]
        raise ApiError(400, "INVALID_REQUEST", "; ".join(problems)) from exc
    if not request.text.strip() and request.media is None:
        raise ApiError(400, "INVALID_REQUEST", "give text and/or media")
    return request


@router.post("/simulate-incoming")
async def simulate_incoming_message(
    request: Request, auth: Auth, db: Db, deps: Deps
) -> dict[str, Any]:
    """Pretend a customer wrote. We call the vendor's real bot and report what happened.

    A finished simulation is always HTTP 200: ``ok`` and ``errorCode`` in the report tell
    whether the bot passed. Only a bad request or a live key is an HTTP error.
    """
    if not auth.is_test:
        raise ApiError(403, "TEST_KEY_REQUIRED", "this endpoint only accepts a wab_test_ key")
    wait = await rate_limit.hit(
        request.app.state.redis, f"sandbox:{auth.vendor.id}", SANDBOX_LIMIT_PER_MINUTE, 60
    )
    if wait:
        raise ApiError(
            429, "RATE_LIMITED", "too many simulations, slow down", {"Retry-After": str(wait)}
        )

    raw = await read_body_limited(request, deps.settings.max_request_body_bytes)
    data = _parse(raw)
    payload = AiReplyRequest(
        chat_id="923001112222@c.us",
        chat_title="Test Customer",
        sender_name="Test Customer",
        sender_number="+923001112222",
        incoming_messages=[IncomingMessage(text=data.text, timestamp="12:00:00", media=data.media)],
    )

    request_id = f"sim-{uuid.uuid4()}"
    started = time.monotonic()
    report = await simulate_incoming(
        db,
        deps,
        auth.vendor,
        payload,
        mode_override=InboundMode.FORWARD,
        request_id=request_id,
    )
    await record_request(
        db,
        vendor_id=auth.vendor.id,
        request_id=request_id,
        kind=RequestKind.SANDBOX,
        mode=report.mode,
        latency_ms=round((time.monotonic() - started) * 1000),
        error_code=report.error_code,
    )
    return {"requestId": request_id, **report_to_dict(report)}
