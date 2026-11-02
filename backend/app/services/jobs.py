"""Creating and reading outbound jobs (plan sections 8.1, 8.10, 17.B)."""

import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ApiError
from app.core.numbers import normalize_customer_number
from app.models import Vendor
from app.models.outbound import JobStatus, JobType, OutboundJob
from app.services import daily_cap
from app.services.job_rules import (
    FINAL_STATUSES,
    IN_FLIGHT_STATUSES,
    MAX_MESSAGE_CHARS,
    PUBLIC_ERROR_CODES,
    TYPE_RULES,
    clamp_ttl,
)


def new_job_id() -> str:
    return f"job_{secrets.token_hex(8)}"


def now_utc() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class CreateResult:
    job: OutboundJob
    created: bool  # False: the same Idempotency-Key was used before, this is the old job


async def find_by_idempotency_key(
    db: AsyncSession, vendor_id: uuid.UUID, key: str
) -> OutboundJob | None:
    return await db.scalar(
        select(OutboundJob).where(
            OutboundJob.vendor_id == vendor_id, OutboundJob.idempotency_key == key
        )
    )


async def open_job_count(db: AsyncSession, vendor_id: uuid.UUID, is_test: bool) -> int:
    open_statuses = [JobStatus.QUEUED.value, *IN_FLIGHT_STATUSES]
    return int(
        await db.scalar(
            select(func.count())
            .select_from(OutboundJob)
            .where(
                OutboundJob.vendor_id == vendor_id,
                OutboundJob.is_test.is_(is_test),
                OutboundJob.status.in_(open_statuses),
            )
        )
        or 0
    )


def validate_request(*, message: str, job_type: str, idempotency_key: str | None) -> None:
    if not message.strip():
        raise ApiError(400, "INVALID_REQUEST", "message must not be empty")
    if len(message) > MAX_MESSAGE_CHARS:
        raise ApiError(
            413, "MESSAGE_TOO_LONG", f"message is longer than {MAX_MESSAGE_CHARS} characters"
        )
    if job_type == JobType.OTP.value and not idempotency_key:
        raise ApiError(400, "INVALID_REQUEST", "Idempotency-Key header is required for otp")


async def create_job(
    db: AsyncSession,
    redis: Redis,
    vendor: Vendor,
    *,
    is_test: bool,
    number: str,
    message: str,
    job_type: str,
    ttl_seconds: int | None,
    idempotency_key: str | None,
    settings: Settings,
    now: datetime | None = None,
) -> CreateResult:
    """Write the job to the database and commit. Everything the vendor can get wrong is
    answered here, before anything is queued."""
    now = now or now_utc()
    # A rollback expires every loaded object (including ``vendor``), so read what we need now.
    vendor_id = vendor.id
    validate_request(message=message, job_type=job_type, idempotency_key=idempotency_key)
    to_number = normalize_customer_number(number, vendor.default_country_code)
    daily_cap_limit = vendor.marketing_daily_cap

    if idempotency_key:
        existing = await find_by_idempotency_key(db, vendor_id, idempotency_key)
        if existing is not None:
            return CreateResult(existing, created=False)

    if await open_job_count(db, vendor_id, is_test) >= settings.max_queued_jobs_per_vendor:
        raise ApiError(503, "QUEUE_FULL", "too many messages are waiting, let some finish first")

    rules = TYPE_RULES[job_type]
    took_cap = False
    if rules.counts_for_daily_cap and not is_test:
        if not await daily_cap.try_take(redis, db, vendor_id, daily_cap_limit, now):
            raise ApiError(
                429,
                "DAILY_CAP_EXCEEDED",
                "today's marketing limit is used up",
                {"Retry-After": str(daily_cap.seconds_until_tomorrow(now))},
            )
        took_cap = True

    job = OutboundJob(
        id=new_job_id(),
        vendor_id=vendor_id,
        is_test=is_test,
        to_number=to_number,
        body=message,
        type=job_type,
        priority=rules.priority,
        status=JobStatus.QUEUED.value,
        attempts=0,
        idempotency_key=idempotency_key,
        available_at=now,
        ttl_expires_at=now + timedelta(seconds=clamp_ttl(ttl_seconds, job_type)),
        created_at=now,
    )
    db.add(job)
    try:
        await db.commit()
    except IntegrityError:
        # Two identical API calls at the same moment: the other one won. Hand back its job.
        await db.rollback()
        if took_cap:
            await daily_cap.give_back(redis, vendor_id, now)
        if idempotency_key:
            winner = await find_by_idempotency_key(db, vendor_id, idempotency_key)
            if winner is not None:
                return CreateResult(winner, created=False)
        raise
    except BaseException:
        await db.rollback()
        if took_cap:
            await daily_cap.give_back(redis, vendor_id, now)
        raise
    return CreateResult(job, created=True)


async def get_for_vendor(
    db: AsyncSession, vendor_id: uuid.UUID, job_id: str, is_test: bool
) -> OutboundJob | None:
    """A vendor only ever sees its own jobs, and a test key only test jobs (and vice versa)."""
    return await db.scalar(
        select(OutboundJob).where(
            OutboundJob.id == job_id,
            OutboundJob.vendor_id == vendor_id,
            OutboundJob.is_test.is_(is_test),
        )
    )


def public_error_code(code: str | None) -> str | None:
    if code is None:
        return None
    return code if code in PUBLIC_ERROR_CODES else "SEND_FAILED"


def status_view(job: OutboundJob) -> dict[str, Any]:
    """GET /v1/message-to-customer/{jobId}. ``dispatched`` is shown as ``sending``."""
    shown = JobStatus.SENDING.value if job.status == JobStatus.DISPATCHED.value else job.status
    return {
        "jobId": job.id,
        "status": shown,
        "sentAt": job.sent_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        if job.sent_at
        else None,
        "errorCode": public_error_code(job.error_code) if job.status == "failed" else None,
    }


def is_final(status: str) -> bool:
    return status in FINAL_STATUSES
