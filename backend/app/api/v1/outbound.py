"""Customer outbound API for vendors (plan section 17.B)."""

import re
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Header, Response

from app.api.deps import Auth, Db, Deps, RuntimeDep
from app.core.errors import ApiError
from app.core.logging import get_logger
from app.schemas.outbound import MessageToCustomer
from app.services import jobs, presence, rate_limit

router = APIRouter(prefix="/v1", tags=["outbound"])
log = get_logger(__name__)

_IDEMPOTENCY_KEY = re.compile(r"[\x21-\x7e]{1,128}")  # printable ASCII, no spaces


@router.post("/message-to-customer", status_code=202)
async def message_to_customer(
    body: MessageToCustomer,
    response: Response,
    auth: Auth,
    db: Db,
    deps: Deps,
    runtime: RuntimeDep,
    idempotency_key: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    vendor = auth.vendor
    vendor_id, is_test = vendor.id, auth.is_test  # a rollback inside would expire ``vendor``
    wait = await rate_limit.hit(
        runtime.redis,
        f"outbound:{vendor_id}",
        deps.settings.outbound_rate_limit_per_minute,
        60,
    )
    if wait:
        raise ApiError(429, "RATE_LIMITED", "too many requests", {"Retry-After": str(wait)})
    if idempotency_key is not None and not _IDEMPOTENCY_KEY.fullmatch(idempotency_key):
        raise ApiError(
            400, "INVALID_REQUEST", "Idempotency-Key must be 1 to 128 visible characters"
        )

    result = await jobs.create_job(
        db,
        runtime.redis,
        vendor,
        is_test=is_test,
        number=body.number,
        message=body.message,
        job_type=body.type.value,
        ttl_seconds=body.ttlSeconds,
        idempotency_key=idempotency_key,
        settings=deps.settings,
        now=datetime.fromtimestamp(runtime.clock(), UTC),
    )
    job = result.job
    # Metadata only. Never the number, the text or an OTP (plan section 14.3).
    log.info(
        "job_accepted" if result.created else "job_replayed",
        job_id=job.id,
        vendor_id=str(vendor_id),
        type=job.type,
        is_test=job.is_test,
    )
    if result.created:
        try:
            await runtime.scheduler.dispatch(str(vendor_id))
        except Exception as exc:  # the job is safe in the database: the sweeper will find it
            log.warning("dispatch_schedule_failed", job_id=job.id, error_type=type(exc).__name__)
    else:
        response.status_code = 200

    online = True if is_test else await presence.is_online(runtime.redis, vendor_id)
    return {
        "success": True,
        "jobId": job.id,
        "status": jobs.status_view(job)["status"],
        "channelOnline": online,
    }


@router.get("/message-to-customer/{job_id}")
async def message_status(job_id: str, auth: Auth, db: Db) -> dict[str, Any]:
    job = await jobs.get_for_vendor(db, auth.vendor.id, job_id, auth.is_test)
    if job is None:
        raise ApiError(404, "NOT_FOUND", "no such message")
    return jobs.status_view(job)
