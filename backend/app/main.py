"""FastAPI application factory. Run with ``uvicorn --factory app.main:create_app``."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from redis.asyncio import Redis

from app.api import health
from app.api.v1 import plugin
from app.core.config import AppEnv, Settings, get_settings
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging, get_logger
from app.core.security import SecretBox
from app.db.session import create_engine, create_session_factory
from app.services.inbound import InboundDeps


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
    app.state.inbound_deps = InboundDeps(
        settings=settings, box=SecretBox(settings.secret_encryption_key.get_secret_value())
    )

    register_error_handlers(app)
    app.include_router(health.router)
    app.include_router(plugin.router)
    return app
