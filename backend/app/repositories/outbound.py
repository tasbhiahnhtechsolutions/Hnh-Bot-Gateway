"""Read-side queries for the admin pages (jobs, devices, webhook deliveries)."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.outbound import Device, JobStatus, OutboundJob, WebhookDelivery


async def list_jobs(
    db: AsyncSession, vendor_id: uuid.UUID, *, status: str | None = None, limit: int = 100
) -> list[OutboundJob]:
    query = (
        select(OutboundJob)
        .where(OutboundJob.vendor_id == vendor_id)
        .order_by(OutboundJob.created_at.desc(), OutboundJob.id.desc())
        .limit(limit)
    )
    if status:
        query = query.where(OutboundJob.status == status)
    return list(await db.scalars(query))


async def list_devices(db: AsyncSession, vendor_id: uuid.UUID) -> list[Device]:
    return list(
        await db.scalars(
            select(Device).where(Device.vendor_id == vendor_id).order_by(Device.last_seen_at.desc())
        )
    )


async def list_deliveries(
    db: AsyncSession, vendor_id: uuid.UUID, *, limit: int = 100
) -> list[WebhookDelivery]:
    return list(
        await db.scalars(
            select(WebhookDelivery)
            .where(WebhookDelivery.vendor_id == vendor_id)
            .order_by(WebhookDelivery.id.desc())
            .limit(limit)
        )
    )


async def cancel_queued(db: AsyncSession, vendor_id: uuid.UUID) -> int:
    """Jobs that were never handed to a plugin are cancelled (suspension, last live key revoked).

    Jobs already in the air are left alone: the plugin may be sending them right now and only
    its answer (or the lease) can say what happened. Returns how many were cancelled.
    """
    result = await db.execute(
        update(OutboundJob)
        .where(
            OutboundJob.vendor_id == vendor_id,
            OutboundJob.is_test.is_(False),
            OutboundJob.status == JobStatus.QUEUED.value,
        )
        .values(
            status=JobStatus.CANCELLED.value,
            finished_at=datetime.now(UTC),
            body=None,
            error_code="CANCELLED",
        )
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]
