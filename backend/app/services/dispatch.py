"""dispatch_next: give the next job of ONE vendor to its plugin (plan section 8.3).

One call = one vendor. It never sleeps: when the vendor must wait (pacing, retry delay) it books
a single alarm and returns, so a worker is free for other vendors. Running it twice is harmless:
the claim is atomic and the database allows one job in flight per vendor.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import Vendor
from app.models.outbound import JobStatus, OutboundJob
from app.models.vendor import OutboundMode, VendorStatus
from app.services import pacing, presence, webhooks
from app.services.job_rules import LEASE_STARTED_SECONDS, TYPE_RULES
from app.services.runtime import Runtime

log = get_logger(__name__)

# Possible answers, mostly for tests and logs.
OFFLINE = "offline"
EMPTY = "empty"
PACED = "paced"
BUSY = "busy"
PAUSED = "paused"
DISPATCHED = "dispatched"
SUSPENDED = "suspended"


def job_payload(job: OutboundJob) -> dict[str, Any]:
    return {
        "jobId": job.id,
        "attempt": job.attempts,
        "to": job.to_number,
        "message": job.body or "",
        "type": job.type,
        "expiresAt": job.ttl_expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


async def expire_due(
    db: AsyncSession, rt: Runtime, now: datetime, vendor_id: uuid.UUID | None = None
) -> int:
    """Queued jobs whose TTL has passed become ``expired``. They are never sent."""
    query = (
        select(OutboundJob)
        .where(OutboundJob.status == JobStatus.QUEUED.value, OutboundJob.ttl_expires_at <= now)
        .order_by(OutboundJob.created_at)
        .limit(500)
        .with_for_update(skip_locked=True)
    )
    if vendor_id is not None:
        query = query.where(OutboundJob.vendor_id == vendor_id)
    jobs = list((await db.scalars(query)).all())
    deliveries: list[int] = []
    vendors: dict[uuid.UUID, Vendor | None] = {}
    for job in jobs:
        job.status = JobStatus.EXPIRED.value
        job.finished_at = now
        if job.vendor_id not in vendors:
            vendors[job.vendor_id] = await db.get(Vendor, job.vendor_id)
        vendor = vendors[job.vendor_id]
        if vendor is not None:
            delivery = await webhooks.enqueue_for_job(db, vendor, job, now)
            if delivery is not None:
                deliveries.append(delivery.id)
    await db.commit()
    for delivery_id in deliveries:
        try:
            await rt.scheduler.deliver_webhook(delivery_id)
        except Exception as exc:
            log.warning("webhook_schedule_failed", error_type=type(exc).__name__)
    return len(jobs)


async def _peek(
    db: AsyncSession, vendor_id: uuid.UUID, is_test: bool, now: datetime
) -> OutboundJob | None:
    return await db.scalar(
        select(OutboundJob)
        .where(
            OutboundJob.vendor_id == vendor_id,
            OutboundJob.is_test.is_(is_test),
            OutboundJob.status == JobStatus.QUEUED.value,
            OutboundJob.available_at <= now,
            OutboundJob.ttl_expires_at > now,
        )
        .order_by(OutboundJob.priority.desc(), OutboundJob.created_at, OutboundJob.id)
        .limit(1)
    )


async def _next_available_in(
    db: AsyncSession, vendor_id: uuid.UUID, is_test: bool, now: datetime
) -> float | None:
    """Seconds until the earliest queued job that is only waiting for its retry time."""
    soonest = await db.scalar(
        select(OutboundJob.available_at)
        .where(
            OutboundJob.vendor_id == vendor_id,
            OutboundJob.is_test.is_(is_test),
            OutboundJob.status == JobStatus.QUEUED.value,
            OutboundJob.available_at > now,
            OutboundJob.ttl_expires_at > now,
        )
        .order_by(OutboundJob.available_at)
        .limit(1)
    )
    return max(0.1, (soonest - now).total_seconds()) if soonest else None


async def _book_alarm(rt: Runtime, vendor_id: uuid.UUID, delay: float, now: datetime) -> None:
    """One alarm per vendor (plan section 8.3). A failure only delays; the sweeper repeats it."""
    try:
        if await pacing.claim_wake_slot(rt.redis, vendor_id, delay, now=now.timestamp()):
            await rt.scheduler.dispatch(str(vendor_id), delay)
    except Exception as exc:
        log.warning("wake_alarm_failed", vendor_id=str(vendor_id), error_type=type(exc).__name__)


async def dispatch_next(
    db: AsyncSession, rt: Runtime, vendor_id: uuid.UUID, *, now: datetime | None = None
) -> dict[str, str]:
    """Run both lines of the vendor: the real plugin and the virtual one (test keys)."""
    now = now or datetime.fromtimestamp(rt.clock(), UTC)
    vendor = await db.get(Vendor, vendor_id)
    if vendor is None:
        return {"real": EMPTY, "test": EMPTY}
    await db.refresh(vendor)  # a fresh read: admin may have paused it a moment ago
    # Read now: a rollback inside the steps below expires every loaded object.
    suspended = vendor.status != VendorStatus.ACTIVE.value
    plugin_enabled = vendor.outbound_mode == OutboundMode.REAL_PLUGIN.value
    await expire_due(db, rt, now, vendor_id)
    if suspended:
        return {"real": SUSPENDED, "test": SUSPENDED}
    return {
        "real": await _dispatch_line(db, rt, vendor_id, False, now, plugin_enabled=plugin_enabled),
        "test": await _dispatch_line(db, rt, vendor_id, True, now, plugin_enabled=True),
    }


async def _dispatch_line(
    db: AsyncSession,
    rt: Runtime,
    vendor_id: uuid.UUID,
    is_test: bool,
    now: datetime,
    *,
    plugin_enabled: bool,
) -> str:
    if not is_test:
        if not plugin_enabled:
            return PAUSED
        if not await presence.is_online_and_wa_connected(rt.redis, vendor_id):
            return OFFLINE  # jobs stay queued; the plugin connecting runs dispatch_next again

    top = await _peek(db, vendor_id, is_test, now)
    if top is None:
        later = await _next_available_in(db, vendor_id, is_test, now)
        if later is not None:
            await _book_alarm(rt, vendor_id, later, now)
            return PACED
        return EMPTY

    if not is_test:
        wait = await pacing.wait_seconds(
            rt.redis, vendor_id, TYPE_RULES[top.type].lane, now=now.timestamp()
        )
        if wait > 0:
            await _book_alarm(rt, vendor_id, wait, now)
            return PACED

    top_id = top.id
    try:
        result = await db.execute(
            update(OutboundJob)
            .where(OutboundJob.id == top_id, OutboundJob.status == JobStatus.QUEUED.value)
            .values(
                status=JobStatus.DISPATCHED.value,
                attempts=OutboundJob.attempts + 1,
                lease_expires_at=now + timedelta(seconds=LEASE_STARTED_SECONDS),
            )
            .returning(OutboundJob.id)
        )
        claimed = result.scalar_one_or_none()
        await db.commit()
    except IntegrityError:  # one_inflight_per_vendor: another job of this vendor is in the air
        await db.rollback()
        return BUSY
    if claimed is None:
        return BUSY  # somebody else claimed this very job a moment ago

    job = await db.get(OutboundJob, claimed)
    assert job is not None
    await db.refresh(job)
    try:
        if is_test:
            await rt.scheduler.run_virtual(job.id, job.attempts, "start", 0)
        else:
            await rt.emitter.emit_job(str(vendor_id), job_payload(job))
        await rt.scheduler.check_lease(job.id, job.attempts, LEASE_STARTED_SECONDS)
    except Exception as exc:
        # The job is already marked dispatched. If the plugin never hears of it, the lease runs
        # out and the job goes back to the queue (sweeper, section 8.5).
        log.warning("dispatch_emit_failed", job_id=job.id, error_type=type(exc).__name__)
    return DISPATCHED
