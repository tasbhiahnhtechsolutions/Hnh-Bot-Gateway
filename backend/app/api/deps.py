"""Shared FastAPI dependencies."""

from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    """One session per request. Services decide when to commit; we roll back on exit."""
    async with request.app.state.session_factory() as session:
        yield session
