"""Readiness checks (database and Redis). Liveness needs no checks."""

from dataclasses import dataclass

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    database: bool
    redis: bool

    @property
    def ok(self) -> bool:
        return self.database and self.redis


async def check_database(engine: AsyncEngine) -> bool:
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        # Never put the exception text in the response: it can contain the DB URL.
        log.warning("readiness_database_failed", error_type=type(exc).__name__)
        return False
    return True


async def check_redis(client: Redis) -> bool:
    try:
        await client.ping()
    except Exception as exc:
        log.warning("readiness_redis_failed", error_type=type(exc).__name__)
        return False
    return True


async def check_readiness(engine: AsyncEngine, redis: Redis) -> ReadinessReport:
    return ReadinessReport(
        database=await check_database(engine),
        redis=await check_redis(redis),
    )
