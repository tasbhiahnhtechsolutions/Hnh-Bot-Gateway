"""Daily marketing cap (plan section 8.10). The limit lives in the database
(``vendors.marketing_daily_cap``); today's counter lives in Redis and is rebuilt from the
database if Redis lost it. The day is the Asia/Karachi day."""

import uuid
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.outbound import JobType, OutboundJob

DAY_ZONE = ZoneInfo("Asia/Karachi")


def today(now: datetime | None = None) -> date:
    return (now or datetime.now(UTC)).astimezone(DAY_ZONE).date()


def seconds_until_tomorrow(now: datetime | None = None) -> int:
    local = (now or datetime.now(UTC)).astimezone(DAY_ZONE)
    midnight = datetime.combine(local.date() + timedelta(days=1), datetime.min.time(), DAY_ZONE)
    return max(1, int((midnight - local).total_seconds()))


def counter_key(vendor_id: uuid.UUID | str, day: date) -> str:
    return f"marketing_count:{vendor_id}:{day.isoformat()}"


async def count_from_db(db: AsyncSession, vendor_id: uuid.UUID, now: datetime | None = None) -> int:
    day = today(now)
    start = datetime.combine(day, datetime.min.time(), DAY_ZONE)
    end = start + timedelta(days=1)
    return int(
        await db.scalar(
            select(func.count())
            .select_from(OutboundJob)
            .where(
                OutboundJob.vendor_id == vendor_id,
                OutboundJob.type == JobType.MARKETING.value,
                OutboundJob.is_test.is_(False),
                OutboundJob.created_at >= start,
                OutboundJob.created_at < end,
            )
        )
        or 0
    )


async def try_take(
    redis: Redis,
    db: AsyncSession,
    vendor_id: uuid.UUID,
    cap: int,
    now: datetime | None = None,
) -> bool:
    """Reserve one marketing slot for today. False when the cap is used up. Call ``give_back``
    if the job then could not be created."""
    key = counter_key(vendor_id, today(now))
    if not await redis.exists(key):
        # Redis was wiped (or this is the first job today): count what the database knows.
        existing = await count_from_db(db, vendor_id, now)
        await redis.set(key, existing, ex=seconds_until_tomorrow(now) + 3600, nx=True)
    used = await redis.incr(key)
    if used > cap:
        await redis.decr(key)
        return False
    return True


async def give_back(redis: Redis, vendor_id: uuid.UUID, now: datetime | None = None) -> None:
    await redis.decr(counter_key(vendor_id, today(now)))


async def used_today(redis: Redis, vendor_id: uuid.UUID, now: datetime | None = None) -> int | None:
    raw = await redis.get(counter_key(vendor_id, today(now)))
    return int(raw) if raw is not None else None
