"""The virtual plugin: answers jobs made with a TEST key, so no WhatsApp message is ever sent
(plan sections 3.4, 7.3). It follows the same protocol as the real plugin: ``started`` first,
then a result, so vendors can test every outcome with magic numbers.

  ...0001  NOT_ON_WHATSAPP (failed)
  ...0002  first attempt WA_NOT_READY (nothing sent, job goes back), second attempt sent
  ...0003  no result ever (the job becomes ``unknown`` after the lease)
  ...0004  sent after 30 seconds
  other    sent after 1 to 3 seconds
"""

import random

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import Vendor
from app.models.outbound import JobStatus, OutboundJob
from app.services import job_results
from app.services.job_rules import ERROR_NOT_ON_WHATSAPP, ERROR_WA_NOT_READY
from app.services.runtime import Runtime

log = get_logger(__name__)

SLOW_SECONDS = 30.0


def _delay_for(number: str) -> float:
    if number.endswith("0004"):
        return SLOW_SECONDS
    return random.uniform(1.0, 3.0)  # noqa: S311 (test behaviour, not security)


async def run_step(db: AsyncSession, rt: Runtime, job_id: str, attempt: int, phase: str) -> str:
    """One step of the virtual plugin. ``phase`` is ``start`` or ``finish``."""
    job = await db.scalar(select(OutboundJob).where(OutboundJob.id == job_id))
    if job is None or not job.is_test or job.attempts != attempt:
        return "skipped"
    vendor = await db.get(Vendor, job.vendor_id)
    if vendor is None:
        return "skipped"
    number = job.to_number

    if phase == "start":
        if number.endswith("0002") and attempt == 1:
            await job_results.apply_result(
                db, rt, vendor, job_id, attempt, "failed", error_code=ERROR_WA_NOT_READY
            )
            return "not_ready"
        started = await job_results.apply_started(db, vendor.id, job_id, attempt)
        if started.outcome is not job_results.Outcome.APPLIED:
            return "skipped"
        if number.endswith("0003"):
            return "silent"  # the job stays ``sending`` until the sweeper makes it ``unknown``
        await rt.scheduler.run_virtual(job_id, attempt, "finish", _delay_for(number))
        return "started"

    if phase == "finish" and job.status in (JobStatus.SENDING.value, JobStatus.DISPATCHED.value):
        if number.endswith("0001"):
            await job_results.apply_result(
                db, rt, vendor, job_id, attempt, "failed", error_code=ERROR_NOT_ON_WHATSAPP
            )
            return "failed"
        await job_results.apply_result(db, rt, vendor, job_id, attempt, "sent", wa_ack=1)
        return "sent"
    return "skipped"
