"""Endpoints used by the Chrome plugin (plan sections 6 and 10)."""

import re
import time
from typing import Annotated, Any

from fastapi import APIRouter, Header, Request
from pydantic import ValidationError

from app.api.body import read_body_limited
from app.api.deps import Auth, Db, Deps
from app.core.errors import ApiError
from app.core.logging import get_logger
from app.models import RequestKind
from app.schemas.ai_reply import CONTRACT_VERSION, AiReplyRequest
from app.services.bot_proxy import BotCallError
from app.services.inbound import new_request_id, process_inbound
from app.services.request_log import record_request
from app.services.wa_number import check_wa_number

router = APIRouter(prefix="/v1", tags=["plugin"])
log = get_logger(__name__)

_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")


@router.get("/plugin/whoami")
async def whoami(auth: Auth) -> dict[str, Any]:
    """Used by the popup after the key is saved: "Connected as <business>"."""
    return {
        "vendorId": str(auth.vendor.id),
        "businessName": auth.vendor.business_name,
        "whatsappNumber": auth.vendor.phone_e164,
        "keyKind": auth.key.kind,
        "inboundMode": auth.vendor.inbound_mode,
        "contractVersion": CONTRACT_VERSION,
    }


def parse_payload(raw: bytes) -> AiReplyRequest:
    try:
        return AiReplyRequest.model_validate_json(raw)
    except ValidationError as exc:
        # Only where and why. Never the input: it can hold customer text and base64 files.
        problems = [
            f"{'.'.join(str(p) for p in e['loc']) or 'body'}: {e['msg']}" for e in exc.errors()[:5]
        ]
        raise ApiError(400, "INVALID_REQUEST", "; ".join(problems)) from exc


@router.post("/ai-reply")
async def ai_reply(
    request: Request,
    auth: Auth,
    db: Db,
    deps: Deps,
    x_wa_number: Annotated[str | None, Header()] = None,
    x_request_id: Annotated[str | None, Header()] = None,
    x_contract_version: Annotated[str | None, Header()] = None,
) -> dict[str, list[str]]:
    started = time.monotonic()
    request_id = x_request_id if x_request_id and _REQUEST_ID.fullmatch(x_request_id) else None
    request_id = request_id or new_request_id()
    outcome: dict[str, Any] = {"mode": auth.vendor.inbound_mode}
    try:
        if x_contract_version not in (None, str(CONTRACT_VERSION)):
            raise ApiError(400, "UNSUPPORTED_CONTRACT_VERSION", "only contract version 1 exists")
        raw = await read_body_limited(request, deps.settings.max_request_body_bytes)
        payload = parse_payload(raw)
        check_wa_number(auth.vendor, x_wa_number, deps.settings.wa_number_mismatch_policy)

        result = await process_inbound(db, deps, auth.vendor, payload, request_id)
        outcome["replies"] = len(result.replies)
        return {"replies": result.replies}
    except ApiError as exc:
        outcome["error_code"] = exc.code
        if isinstance(exc, BotCallError):
            outcome["bot_status"] = exc.report.response_status
        raise
    finally:
        # Metadata only: no message text, no files, no replies (plan section 14).
        latency_ms = round((time.monotonic() - started) * 1000)
        log.info(
            "ai_reply",
            request_id=request_id,
            vendor_id=str(auth.vendor.id),
            latency_ms=latency_ms,
            **outcome,
        )
        await record_request(
            db,
            vendor_id=auth.vendor.id,
            request_id=request_id,
            kind=RequestKind.AI_REPLY,
            mode=auth.vendor.inbound_mode,
            latency_ms=latency_ms,
            error_code=outcome.get("error_code"),
        )
