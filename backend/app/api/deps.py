"""Shared FastAPI dependencies."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.repositories import api_keys
from app.services import rate_limit
from app.services.auth import AuthContext, authenticate
from app.services.inbound import InboundDeps

LAST_USED_REFRESH = timedelta(seconds=60)


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    """One session per request. Services decide when to commit; we roll back on exit."""
    async with request.app.state.session_factory() as session:
        yield session


def get_inbound_deps(request: Request) -> InboundDeps:
    deps: InboundDeps = request.app.state.inbound_deps
    return deps


async def get_auth(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    x_api_key: Annotated[str | None, Header()] = None,
) -> AuthContext:
    """Key check and vendor check (plan section 6). The key is read from the header only."""
    settings = request.app.state.settings
    redis = request.app.state.redis
    client_ip = request.client.host if request.client else "unknown"

    wait = await rate_limit.blocked_for(redis, client_ip, settings.auth_fail_limit_per_minute)
    if wait:
        raise ApiError(
            429, "RATE_LIMITED", "too many wrong keys, try again later", {"Retry-After": str(wait)}
        )
    try:
        ctx = await authenticate(db, x_api_key)
    except ApiError as exc:
        if exc.code == "INVALID_KEY":
            await rate_limit.record_failure(redis, client_ip)
        raise

    now = datetime.now(UTC)
    if ctx.key.last_used_at is None or now - ctx.key.last_used_at > LAST_USED_REFRESH:
        await api_keys.touch_last_used(db, ctx.key.id)
        await db.commit()
    return ctx


Auth = Annotated[AuthContext, Depends(get_auth)]
Db = Annotated[AsyncSession, Depends(get_db)]
Deps = Annotated[InboundDeps, Depends(get_inbound_deps)]
