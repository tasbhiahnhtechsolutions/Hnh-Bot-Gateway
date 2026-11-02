"""Per-vendor pacing and wake alarms in Redis (plan sections 8.3, 8.10).

Keys (all expire after 60 s; losing them only means one job goes without its gap):
  pace:vendor:{id}:fast   unix time until which otp/transactional jobs wait
  pace:vendor:{id}:slow   unix time until which marketing jobs wait
  wake:vendor:{id}        unix time of the one pending "look again" alarm
"""

import random
import time
import uuid
from collections.abc import Callable

from redis.asyncio import Redis

from app.services.job_rules import FAST_GAP, SLOW_GAP

KEY_TTL_SECONDS = 60

Rng = Callable[[float, float], float]


def pace_key(vendor_id: uuid.UUID | str, lane: str) -> str:
    return f"pace:vendor:{vendor_id}:{lane}"


def wake_key(vendor_id: uuid.UUID | str) -> str:
    return f"wake:vendor:{vendor_id}"


async def set_gaps(
    redis: Redis,
    vendor_id: uuid.UUID | str,
    *,
    now: float | None = None,
    rng: Rng = random.uniform,
) -> tuple[float, float]:
    """A job finished: both lanes start their gap. Returns (fast_until, slow_until)."""
    now = time.time() if now is None else now
    fast_until = now + rng(*FAST_GAP)
    slow_until = now + rng(*SLOW_GAP)
    async with redis.pipeline(transaction=False) as pipe:
        pipe.set(pace_key(vendor_id, "fast"), fast_until, ex=KEY_TTL_SECONDS)
        pipe.set(pace_key(vendor_id, "slow"), slow_until, ex=KEY_TTL_SECONDS)
        await pipe.execute()
    return fast_until, slow_until


async def wait_seconds(
    redis: Redis, vendor_id: uuid.UUID | str, lane: str, *, now: float | None = None
) -> float:
    """How long a job of this lane still has to wait. 0 when it may go."""
    now = time.time() if now is None else now
    raw = await redis.get(pace_key(vendor_id, lane))
    if raw is None:
        return 0.0
    return max(0.0, float(raw) - now)


async def claim_wake_slot(
    redis: Redis, vendor_id: uuid.UUID | str, delay: float, *, now: float | None = None
) -> bool:
    """True when the caller must schedule the alarm. One alarm per vendor: a pending alarm that
    rings at the same time or earlier already covers this request (plan section 8.3)."""
    now = time.time() if now is None else now
    wake_at = now + delay
    key = wake_key(vendor_id)
    script = """
    local existing = tonumber(redis.call('GET', KEYS[1]) or '0')
    if existing > 0 and existing <= tonumber(ARGV[1]) and existing > tonumber(ARGV[3]) then
        return 0
    end
    redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2])
    return 1
    """
    result = await redis.eval(script, 1, key, str(wake_at), str(int(delay) + 10), str(now))  # type: ignore[misc]
    return bool(result)


async def clear_wake(redis: Redis, vendor_id: uuid.UUID | str) -> None:
    await redis.delete(wake_key(vendor_id))
