"""What the plugin tells us about a job: started, sent, failed (plan sections 8.4, 8.6, 8.7).

Rules that keep an OTP from going out twice (plan 8.7):
* ``started`` -> the message may be on its way. From now on a missing result is ``unknown`` and
  is never retried.
* A ``sent`` report is always believed, whatever state we are in (the plugin really sent it).
* A ``failed`` report of an old attempt is ignored: it must not kill a newer attempt.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import Vendor
from app.models.outbound import JobStatus, OutboundJob
from app.services import pacing, webhooks
from app.services.job_rules import (
    ERROR_NOT_ON_WHATSAPP,
    ERROR_SEND_FAILED,
    ERROR_WA_NOT_READY,
    LEASE_RESULT_SECONDS,
    MAX_ATTEMPTS,
    REQUEUE_DELAY_SECONDS,
    TYPE_RULES,
    can_transition,
)
from app.services.jobs import now_utc
from app.services.runtime import Runtime

log = get_logger(__name__)


class Outcome(StrEnum):
    APPLIED = "applied"
    DUPLICATE = "duplicate"  # same news twice: nothing changes
    IGNORED = "ignored"  # old or out-of-order news that must not change anything
    REQUEUED = "requeued"
    NOT_FOUND = "not_found"  # no such job for this vendor: a security matter, not an error


@dataclass(frozen=True, slots=True)
class Applied:
    outcome: Outcome
    status: str | None = None


async def _lock(db: AsyncSession, vendor_id: uuid.UUID, job_id: str) -> OutboundJob | None:
    return await db.scalar(
        select(OutboundJob)
        .where(OutboundJob.id == job_id, OutboundJob.vendor_id == vendor_id)
        .with_for_update()
    )


async def _leave(db: AsyncSession, job: OutboundJob, outcome: Outcome) -> Applied:
    """Answer without changing anything. The status is read before the rollback expires ``job``."""
    status = job.status
    await db.rollback()
    return Applied(outcome, status)


def _finish(job: OutboundJob, status: str, now: datetime) -> None:
    assert can_transition(job.status, status), f"{job.status} -> {status} is not allowed"
    job.status = status
    job.finished_at = now
    job.lease_expires_at = None


async def apply_started(
    db: AsyncSession,
    vendor_id: uuid.UUID,
    job_id: str,
    attempt: int,
    now: datetime | None = None,
) -> Applied:
    now = now or now_utc()
    job = await _lock(db, vendor_id, job_id)
    if job is None:
        await db.rollback()
        return Applied(Outcome.NOT_FOUND)
    if job.status == JobStatus.SENDING.value and attempt == job.attempts:
        return await _leave(db, job, Outcome.DUPLICATE)
    if job.status != JobStatus.DISPATCHED.value or attempt != job.attempts:
        # Late "started" for a job we already took back, or for a finished one. A result
        # may still follow and will be believed.
        return await _leave(db, job, Outcome.IGNORED)
    assert can_transition(job.status, JobStatus.SENDING.value)
    job.status = JobStatus.SENDING.value
    job.lease_expires_at = now + timedelta(seconds=LEASE_RESULT_SECONDS)
    await db.commit()
    return Applied(Outcome.APPLIED, job.status)


async def apply_result(
    db: AsyncSession,
    rt: Runtime,
    vendor: Vendor,
    job_id: str,
    attempt: int,
    status: str,
    *,
    wa_ack: int | None = None,
    error_code: str | None = None,
    now: datetime | None = None,
) -> Applied:
    """``status`` is what the plugin reports: ``sent``, ``failed`` or ``expired``."""
    now = now or datetime.fromtimestamp(rt.clock(), UTC)
    job = await _lock(db, vendor.id, job_id)
    if job is None:
        await db.rollback()
        return Applied(Outcome.NOT_FOUND)

    if status == "sent":
        return await _apply_sent(db, rt, vendor, job, wa_ack, now)
    if status in ("failed", "expired"):
        return await _apply_failure(db, rt, vendor, job, attempt, status, error_code, now)
    return await _leave(db, job, Outcome.IGNORED)


async def _after_commit(
    rt: Runtime,
    vendor: Vendor,
    job: OutboundJob,
    delivery_id: int | None,
    now: datetime,
    *,
    paced: bool,
) -> None:
    """Things that happen after the database says yes. Each may fail without harm: the
    sweepers (section 8.5) repeat them."""
    try:
        if paced and not job.is_test:
            await pacing.set_gaps(rt.redis, vendor.id, now=now.timestamp())
        if delivery_id is not None:
            await rt.scheduler.deliver_webhook(delivery_id)
        await rt.scheduler.dispatch(str(vendor.id))
    except Exception as exc:
        log.warning("after_result_failed", job_id=job.id, error_type=type(exc).__name__)


async def _apply_sent(
    db: AsyncSession,
    rt: Runtime,
    vendor: Vendor,
    job: OutboundJob,
    wa_ack: int | None,
    now: datetime,
) -> Applied:
    if job.status == JobStatus.SENT.value:
        return await _leave(db, job, Outcome.DUPLICATE)
    if not can_transition(job.status, JobStatus.SENT.value):
        # expired / cancelled / failed: we had already given up. Keep our record, log the truth.
        log.warning("sent_after_final", job_id=job.id, status=job.status)
        return await _leave(db, job, Outcome.IGNORED)
    job.sent_at = now
    job.wa_ack = wa_ack
    job.error_code = None
    _finish(job, JobStatus.SENT.value, now)
    delivery = await webhooks.enqueue_for_job(db, vendor, job, now)
    await db.commit()
    await _after_commit(rt, vendor, job, delivery.id if delivery else None, now, paced=True)
    return Applied(Outcome.APPLIED, job.status)


async def _apply_failure(
    db: AsyncSession,
    rt: Runtime,
    vendor: Vendor,
    job: OutboundJob,
    attempt: int,
    status: str,
    error_code: str | None,
    now: datetime,
) -> Applied:
    if job.status in (
        JobStatus.SENT.value,
        JobStatus.FAILED.value,
        JobStatus.EXPIRED.value,
        JobStatus.CANCELLED.value,
    ):
        return await _leave(db, job, Outcome.DUPLICATE)
    if attempt != job.attempts or job.status not in (
        JobStatus.DISPATCHED.value,
        JobStatus.SENDING.value,
        JobStatus.UNKNOWN.value,
    ):
        return await _leave(
            db, job, Outcome.IGNORED
        )  # an old attempt's failure must not hurt the current one

    if status == "expired":
        if job.status != JobStatus.DISPATCHED.value:
            return await _leave(db, job, Outcome.IGNORED)
        _finish(job, JobStatus.EXPIRED.value, now)
        return await _commit_final(db, rt, vendor, job, now, paced=False)

    code = error_code or ERROR_SEND_FAILED
    rules = TYPE_RULES[job.type]
    not_sent_yet = job.status == JobStatus.DISPATCHED.value
    retry = False
    delay = float(REQUEUE_DELAY_SECONDS)
    if code == ERROR_WA_NOT_READY:
        retry = not_sent_yet  # nothing was sent: safe for every type, even an OTP
    elif code != ERROR_NOT_ON_WHATSAPP and rules.auto_retry:
        retry = job.status != JobStatus.UNKNOWN.value
        delay = 5.0 * job.attempts  # transactional and marketing: a short, growing pause
    if (
        retry
        and job.attempts < MAX_ATTEMPTS
        and job.ttl_expires_at > now + timedelta(seconds=delay)
    ):
        assert can_transition(job.status, JobStatus.QUEUED.value)
        job.status = JobStatus.QUEUED.value
        job.lease_expires_at = None
        job.available_at = now + timedelta(seconds=delay)
        job.error_code = code
        await db.commit()
        await _requeue_after_commit(rt, vendor, job, delay, now, paced=code != ERROR_WA_NOT_READY)
        return Applied(Outcome.REQUEUED, job.status)

    job.error_code = ERROR_NOT_ON_WHATSAPP if code == ERROR_NOT_ON_WHATSAPP else ERROR_SEND_FAILED
    _finish(job, JobStatus.FAILED.value, now)
    return await _commit_final(db, rt, vendor, job, now, paced=code != ERROR_WA_NOT_READY)


async def _commit_final(
    db: AsyncSession,
    rt: Runtime,
    vendor: Vendor,
    job: OutboundJob,
    now: datetime,
    *,
    paced: bool,
) -> Applied:
    delivery = await webhooks.enqueue_for_job(db, vendor, job, now)
    await db.commit()
    await _after_commit(rt, vendor, job, delivery.id if delivery else None, now, paced=paced)
    return Applied(Outcome.APPLIED, job.status)


async def _requeue_after_commit(
    rt: Runtime, vendor: Vendor, job: OutboundJob, delay: float, now: datetime, *, paced: bool
) -> None:
    try:
        if paced and not job.is_test:
            await pacing.set_gaps(rt.redis, vendor.id, now=now.timestamp())
        await rt.scheduler.dispatch(str(vendor.id), delay)
    except Exception as exc:
        log.warning("requeue_schedule_failed", job_id=job.id, error_type=type(exc).__name__)
