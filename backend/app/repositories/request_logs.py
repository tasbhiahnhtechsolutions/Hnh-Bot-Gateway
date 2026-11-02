import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.request_log import RequestLog


async def add(db: AsyncSession, row: RequestLog) -> RequestLog:
    db.add(row)
    await db.flush()
    return row


async def list_recent(
    db: AsyncSession, *, vendor_id: uuid.UUID | None = None, limit: int = 100
) -> list[RequestLog]:
    query = select(RequestLog).order_by(RequestLog.id.desc()).limit(limit)
    if vendor_id is not None:
        query = query.where(RequestLog.vendor_id == vendor_id)
    return list(await db.scalars(query))


async def last_seen_by_vendor(db: AsyncSession) -> dict[uuid.UUID, datetime]:
    """Latest real plugin request per vendor ("Last seen" in the vendors list)."""
    rows = await db.execute(
        select(RequestLog.vendor_id, func.max(RequestLog.created_at))
        .where(RequestLog.kind == "ai_reply")
        .group_by(RequestLog.vendor_id)
    )
    return {row[0]: row[1] for row in rows.all()}


async def counts_since(db: AsyncSession, since: datetime) -> dict[str, int]:
    """``{"ok": n, "error": n}`` for requests created at or after ``since``."""
    rows = await db.execute(
        select(RequestLog.status, func.count())
        .where(RequestLog.created_at >= since)
        .group_by(RequestLog.status)
    )
    counts = {"ok": 0, "error": 0}
    for status, count in rows.all():
        counts[status] = count
    return counts
