"""Safety nets that Celery Beat runs every 30 seconds (plan section 8.5), plus the lease check.

Nothing here is the main path. If everything else works these find nothing to do.
"""

import uuid
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import Vendor
from app.models.outbound import JobStatus, OutboundJob, WebhookDelivery, WebhookStatus
from app.services import presence, webhooks
from app.services.dispatch import expire_due
from app.services.job_rules import BODY_RETENTION_HOURS
from app.services.jobs import now_utc
from app.services.runtime import Runtime

log = get_logger(__name__)


async def _wake(rt: Runtime, vendor_ids: set[uuid.UUID]) -> None:
    for vendor_id in vendor_ids:
        try:
            await rt.scheduler.dispatch(str(vendor_id))
        except Exception as exc:
            log.warning("sweeper_wake_failed", error_type=type(exc).__name__)


async def sweep_leases(
    db: AsyncSession, rt: Runtime, now: datetime | None = None
) -> dict[str, int]:
    """``dispatched`` for too long -> back to ``queued`` (safe: nothing was started).
    ``sending`` for too long -> ``unknown`` (never retried: it may have been sent)."""
    now = now or now_utc()
    requeued = await db.execute(
        update(OutboundJob)
        .where(
            OutboundJob.status == JobStatus.DISPATCHED.value,
            OutboundJob.lease_expires_at <= now,
        )
        .values(status=JobStatus.QUEUED.value, lease_expires_at=None, available_at=now)
        .returning(OutboundJob.id, OutboundJob.vendor_id)
    )
    requeued_rows = requeued.all()

    stuck = list(
        (
            await db.scalars(
                select(OutboundJob)
                .where(
                    OutboundJob.status == JobStatus.SENDING.value,
                    OutboundJob.lease_expires_at <= now,
                )
                .with_for_update(skip_locked=True)
            )
        ).all()
    )
    deliveries: list[int] = []
    for job in stuck:
        job.status = JobStatus.UNKNOWN.value
        job.finished_at = now
        job.lease_expires_at = None
        job.error_code = "NO_RESULT"
        vendor = await db.get(Vendor, job.vendor_id)
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
    await _wake(rt, {row.vendor_id for row in requeued_rows} | {j.vendor_id for j in stuck})
    return {"requeued": len(requeued_rows), "unknown": len(stuck)}


async def check_lease(
    db: AsyncSession, rt: Runtime, job_id: str, attempt: int, now: datetime | None = None
) -> str:
    """Delayed check after dispatch: no "started" yet -> back to the queue."""
    now = now or now_utc()
    result = await db.execute(
        update(OutboundJob)
        .where(
            OutboundJob.id == job_id,
            OutboundJob.attempts == attempt,
            OutboundJob.status == JobStatus.DISPATCHED.value,
            OutboundJob.lease_expires_at <= now,
        )
        .values(status=JobStatus.QUEUED.value, lease_expires_at=None, available_at=now)
        .returning(OutboundJob.vendor_id)
    )
    vendor_id = result.scalar_one_or_none()
    await db.commit()
    if vendor_id is None:
        return "ok"
    await _wake(rt, {vendor_id})
    return "requeued"


async def expire_jobs(db: AsyncSession, rt: Runtime, now: datetime | None = None) -> int:
    return await expire_due(db, rt, now or now_utc())


async def reconcile_idle_vendors(db: AsyncSession, rt: Runtime, now: datetime | None = None) -> int:
    """Vendors with queued jobs and nobody working on them get a fresh ``dispatch_next``.
    This is what rescues jobs when the "wake up" message was lost (Redis down, worker killed)."""
    now = now or now_utc()
    rows = await db.execute(
        select(OutboundJob.vendor_id)
        .where(
            OutboundJob.status == JobStatus.QUEUED.value,
            OutboundJob.available_at <= now,
            OutboundJob.ttl_expires_at > now,
        )
        .distinct()
    )
    candidates = {row[0] for row in rows.all()}
    in_flight = {
        row[0]
        for row in (
            await db.execute(
                select(OutboundJob.vendor_id)
                .where(
                    OutboundJob.status.in_([JobStatus.DISPATCHED.value, JobStatus.SENDING.value]),
                    OutboundJob.is_test.is_(False),
                )
                .distinct()
            )
        ).all()
    }
    woken = 0
    for vendor_id in candidates - in_flight:
        # A vendor whose plugin is offline cannot be helped. The test line has no plugin.
        online = await presence.is_online_and_wa_connected(rt.redis, vendor_id)
        has_test = await db.scalar(
            select(OutboundJob.id)
            .where(
                OutboundJob.vendor_id == vendor_id,
                OutboundJob.is_test.is_(True),
                OutboundJob.status == JobStatus.QUEUED.value,
            )
            .limit(1)
        )
        if online or has_test:
            await _wake(rt, {vendor_id})
            woken += 1
    return woken


async def purge_bodies(db: AsyncSession, now: datetime | None = None) -> int:
    """24 hours after the final status the message text is wiped (plan section 8.5)."""
    now = now or now_utc()
    result = await db.execute(
        update(OutboundJob)
        .where(
            OutboundJob.body.is_not(None),
            OutboundJob.finished_at <= now - timedelta(hours=BODY_RETENTION_HOURS),
        )
        .values(body=None)
        .returning(OutboundJob.id)
    )
    count = len(result.all())
    await db.commit()
    return count


async def due_webhooks(db: AsyncSession, rt: Runtime, now: datetime | None = None) -> int:
    """Pending deliveries whose time has come but whose Celery message was lost."""
    now = now or now_utc()
    ids = list(
        (
            await db.scalars(
                select(WebhookDelivery.id)
                .where(
                    WebhookDelivery.status == WebhookStatus.PENDING.value,
                    WebhookDelivery.next_attempt_at <= now - timedelta(seconds=60),
                )
                .limit(200)
            )
        ).all()
    )
    for delivery_id in ids:
        try:
            await rt.scheduler.deliver_webhook(delivery_id)
        except Exception as exc:
            log.warning("webhook_schedule_failed", error_type=type(exc).__name__)
    return len(ids)
