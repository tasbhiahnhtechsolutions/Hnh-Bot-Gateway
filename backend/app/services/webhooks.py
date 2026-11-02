"""Status webhooks to the vendor (plan section 17.B.8): signed, retried, never lost silently."""

import json
import secrets
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ApiError
from app.core.logging import get_logger
from app.core.security import SecretBox
from app.core.signing import signature_header
from app.models import Vendor
from app.models.outbound import OutboundJob, WebhookDelivery, WebhookStatus
from app.services.bot_proxy import TooLargeError, post_pinned
from app.services.bot_url import Resolver, resolve_host, validate_bot_url
from app.services.job_rules import WEBHOOK_EVENTS, WEBHOOK_MAX_ATTEMPTS, WEBHOOK_RETRY_DELAYS
from app.services.jobs import public_error_code

log = get_logger(__name__)


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_payload(job: OutboundJob, event: str, event_id: str, now: datetime) -> dict[str, Any]:
    return {
        "eventId": event_id,
        "event": event,
        "jobId": job.id,
        "number": job.to_number,
        "status": job.status,
        "at": _iso(job.sent_at if job.status == "sent" and job.sent_at else now),
        "errorCode": public_error_code(job.error_code) if job.status == "failed" else None,
    }


async def enqueue_for_job(
    db: AsyncSession, vendor: Vendor, job: OutboundJob, now: datetime
) -> WebhookDelivery | None:
    """Add a delivery row (in the caller's transaction) when this job just reached a status the
    vendor is told about. Returns None when there is nothing to send."""
    event = WEBHOOK_EVENTS.get(job.status)
    if event is None or not vendor.status_webhook_url:
        return None
    event_id = f"evt_{secrets.token_hex(8)}"
    delivery = WebhookDelivery(
        vendor_id=vendor.id,
        job_id=job.id,
        event_id=event_id,
        event=event,
        payload=build_payload(job, event, event_id, now),
        status=WebhookStatus.PENDING.value,
        attempts=0,
        next_attempt_at=now,
        created_at=now,
    )
    db.add(delivery)
    await db.flush()
    return delivery


async def attempt_delivery(
    db: AsyncSession,
    delivery_id: int,
    *,
    settings: Settings,
    box: SecretBox,
    transport: httpx.AsyncBaseTransport | None = None,
    resolver: Resolver = resolve_host,
    now: datetime | None = None,
) -> float | None:
    """Make one attempt. Returns the delay (seconds) until the next try, or None when this
    delivery is finished (delivered, given up, or not pending)."""
    now = now or datetime.now(UTC)
    delivery = await db.scalar(
        select(WebhookDelivery).where(WebhookDelivery.id == delivery_id).with_for_update()
    )
    if delivery is None or delivery.status != WebhookStatus.PENDING.value:
        await db.rollback()
        return None
    vendor = await db.get(Vendor, delivery.vendor_id)
    if vendor is None or not vendor.status_webhook_url:
        delivery.status = WebhookStatus.FAILED.value
        delivery.last_error = "NO_WEBHOOK_URL"
        await db.commit()
        return None

    raw = json.dumps(delivery.payload, ensure_ascii=False, separators=(",", ":")).encode()
    timestamp = int(time.time())
    headers = {
        "Content-Type": "application/json",
        "X-Gateway-Timestamp": str(timestamp),
        "X-Gateway-Signature": signature_header(
            [box.decrypt(vendor.webhook_secret_enc)], timestamp, raw
        ),
        "X-Event-Id": delivery.event_id,
        "User-Agent": "vendor-gateway/1",
    }
    status_code: int | None = None
    error: str | None = None
    try:
        ips = await validate_bot_url(
            vendor.status_webhook_url,
            allow_private=settings.allow_private_bot_urls,
            resolver=resolver,
        )
        status_code, _ = await post_pinned(
            vendor.status_webhook_url,
            headers,
            raw,
            ips,
            transport,
            timeout_seconds=settings.webhook_timeout_seconds,
            max_bytes=64 * 1024,
        )
        if not 200 <= status_code < 300:
            error = f"HTTP_{status_code}"
    except ApiError:
        error = "URL_NOT_ALLOWED"  # never retry into an address we refuse to call
    except TooLargeError:
        error = "RESPONSE_TOO_LARGE"
    except httpx.TimeoutException:
        error = "TIMEOUT"
    except httpx.HTTPError as exc:
        error = f"UNREACHABLE_{type(exc).__name__}"[:100]

    delivery.attempts += 1
    delivery.last_status_code = status_code
    delivery.last_error = error
    if error is None:
        delivery.status = WebhookStatus.DELIVERED.value
        delivery.delivered_at = now
        await db.commit()
        return None
    if error == "URL_NOT_ALLOWED" or delivery.attempts >= WEBHOOK_MAX_ATTEMPTS:
        delivery.status = WebhookStatus.FAILED.value
        await db.commit()
        log.warning("webhook_given_up", delivery_id=delivery.id, error=error)
        return None
    delay = WEBHOOK_RETRY_DELAYS[delivery.attempts - 1]
    delivery.next_attempt_at = now + timedelta(seconds=delay)
    await db.commit()
    return float(delay)


async def replay(db: AsyncSession, delivery_id: int, vendor_id: uuid.UUID) -> bool:
    """Admin button: try a failed (or delivered) delivery again from scratch."""
    delivery = await db.scalar(
        select(WebhookDelivery)
        .where(WebhookDelivery.id == delivery_id, WebhookDelivery.vendor_id == vendor_id)
        .with_for_update()
    )
    if delivery is None or delivery.status == WebhookStatus.PENDING.value:
        await db.rollback()
        return False
    delivery.status = WebhookStatus.PENDING.value
    delivery.attempts = 0
    delivery.next_attempt_at = datetime.now(UTC)
    delivery.last_error = None
    await db.commit()
    return True
