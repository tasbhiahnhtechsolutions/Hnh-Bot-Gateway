"""Celery tasks. Each one is a thin wrapper: it opens a database session, builds a runtime and
runs the matching service function (``app/services``). Every task handles one vendor or one job
and never sleeps (plan section 8.3)."""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.core.security import SecretBox
from app.realtime.emitter import RedisEmitter
from app.services import dispatch, sweepers, virtual_plugin, webhooks
from app.services.runtime import Runtime
from app.services.scheduling import CeleryScheduler
from app.worker.celery_app import celery_app

log = get_logger(__name__)


async def _with_runtime(work: Callable[[AsyncSession, Runtime], Awaitable[Any]]) -> Any:
    """A fresh engine and Redis client per task: ``asyncio.run`` makes a new event loop each
    time, and connections must not be carried from one loop to another."""
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import create_async_engine

    settings = get_settings()
    configure_logging(settings.log_level, json_logs=True)
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    redis = Redis.from_url(settings.redis_url)
    emitter = RedisEmitter(settings.redis_url)
    rt = Runtime(
        settings=settings,
        redis=redis,
        scheduler=CeleryScheduler(celery_app),
        emitter=emitter,
        box=SecretBox(settings.secret_encryption_key.get_secret_value()),
    )
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            return await work(db, rt)
    finally:
        await emitter.close()
        await redis.aclose()
        await engine.dispose()


@celery_app.task(name="gateway.dispatch_next", bind=True, soft_time_limit=20)
def dispatch_next(self: Any, vendor_id: str) -> dict[str, str]:
    result: dict[str, str] = asyncio.run(
        _with_runtime(lambda db, rt: dispatch.dispatch_next(db, rt, uuid.UUID(vendor_id)))
    )
    return result


@celery_app.task(name="gateway.check_lease", soft_time_limit=20)
def check_lease(job_id: str, attempt: int) -> str:
    result: str = asyncio.run(
        _with_runtime(lambda db, rt: sweepers.check_lease(db, rt, job_id, attempt))
    )
    return result


@celery_app.task(name="gateway.run_virtual", soft_time_limit=20)
def run_virtual(job_id: str, attempt: int, phase: str) -> str:
    result: str = asyncio.run(
        _with_runtime(lambda db, rt: virtual_plugin.run_step(db, rt, job_id, attempt, phase))
    )
    return result


@celery_app.task(name="gateway.deliver_webhook", soft_time_limit=30)
def deliver_webhook(delivery_id: int) -> str:
    async def work(db: AsyncSession, rt: Runtime) -> str:
        delay = await webhooks.attempt_delivery(db, delivery_id, settings=rt.settings, box=rt.box)
        if delay is not None:
            await rt.scheduler.deliver_webhook(delivery_id, delay)
            return "retry"
        return "done"

    result: str = asyncio.run(_with_runtime(work))
    return result


@celery_app.task(name="gateway.sweep_leases")
def sweep_leases() -> dict[str, int]:
    result: dict[str, int] = asyncio.run(_with_runtime(sweepers.sweep_leases))
    return result


@celery_app.task(name="gateway.expire_jobs")
def expire_jobs() -> int:
    result: int = asyncio.run(_with_runtime(sweepers.expire_jobs))
    return result


@celery_app.task(name="gateway.reconcile_idle_vendors")
def reconcile_idle_vendors() -> int:
    result: int = asyncio.run(_with_runtime(sweepers.reconcile_idle_vendors))
    return result


@celery_app.task(name="gateway.due_webhooks")
def due_webhooks() -> int:
    result: int = asyncio.run(_with_runtime(sweepers.due_webhooks))
    return result


@celery_app.task(name="gateway.purge_bodies")
def purge_bodies() -> int:
    result: int = asyncio.run(_with_runtime(lambda db, _rt: sweepers.purge_bodies(db)))
    return result
