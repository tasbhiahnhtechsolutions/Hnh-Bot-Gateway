"""Wrong-key rate limit per client IP (plan section 4). Fails open if Redis is down."""

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.logging import get_logger

log = get_logger(__name__)

WINDOW_SECONDS = 60


def _key(client_ip: str) -> str:
    return f"authfail:{client_ip}"


async def blocked_for(redis: Redis, client_ip: str, limit: int) -> int:
    """Seconds the client must wait, or 0 when it may try."""
    try:
        count = await redis.get(_key(client_ip))
        if count is None or int(count) < limit:
            return 0
        ttl = await redis.ttl(_key(client_ip))
    except RedisError as exc:
        log.warning("rate_limit_unavailable", error_type=type(exc).__name__)
        return 0
    return max(int(ttl), 1)


async def record_failure(redis: Redis, client_ip: str) -> None:
    try:
        key = _key(client_ip)
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, WINDOW_SECONDS)
    except RedisError as exc:
        log.warning("rate_limit_unavailable", error_type=type(exc).__name__)
