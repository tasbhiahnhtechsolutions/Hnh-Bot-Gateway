"""FastAPI application factory. Run with ``uvicorn --factory app.main:create_app``."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import socketio
from fastapi import FastAPI
from redis.asyncio import Redis

from app.admin.router import register_admin
from app.admin.security import install_admin_security, parse_allowlist
from app.api import health
from app.api.v1 import outbound, plugin, sandbox
from app.core.config import AppEnv, Settings, get_settings
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging, get_logger
from app.core.security import SecretBox
from app.db.session import create_engine, create_session_factory
from app.realtime.emitter import ServerEmitter
from app.realtime.hub import Hub
from app.services.inbound import InboundDeps
from app.services.runtime import Runtime
from app.services.scheduling import CeleryScheduler
from app.worker.factory import make_celery


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, json_logs=settings.app_env is not AppEnv.DEV)

    engine = create_engine(settings.database_url)
    redis = Redis.from_url(settings.redis_url)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        get_logger(__name__).info("startup", app_env=settings.app_env.value)
        yield
        await redis.aclose()
        await engine.dispose()

    # Docs are for developers only; prod and staging do not expose the schema.
    expose_docs = settings.app_env in (AppEnv.DEV, AppEnv.TEST)
    app = FastAPI(
        title="Vendor Gateway",
        lifespan=lifespan,
        docs_url="/docs" if expose_docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if expose_docs else None,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)
    app.state.redis = redis
    box = SecretBox(settings.secret_encryption_key.get_secret_value())
    app.state.inbound_deps = InboundDeps(settings=settings, box=box)

    # Outbound (plan section 8): the Socket.IO hub lives in this process, Celery workers reach
    # it through Redis. Tests replace ``app.state.runtime`` with fakes.
    hub = Hub(app)
    app.state.hub = hub
    app.state.runtime = Runtime(
        settings=settings,
        redis=redis,
        scheduler=CeleryScheduler(make_celery(settings)),
        emitter=ServerEmitter(hub.sio),
        box=box,
    )
    app.mount("/socket.io", socketio.ASGIApp(hub.sio))

    if settings.app_env in (AppEnv.STAGING, AppEnv.PROD) and not settings.admin_ip_allowlist:
        get_logger(__name__).warning("admin_ip_allowlist_empty", note="admin panel open to any IP")

    register_error_handlers(app)
    install_admin_security(app, parse_allowlist(settings.admin_ip_allowlist))
    register_admin(app)
    app.include_router(health.router)
    app.include_router(plugin.router)
    app.include_router(sandbox.router)
    app.include_router(outbound.router)
    return app
