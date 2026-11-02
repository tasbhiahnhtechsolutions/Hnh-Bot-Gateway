import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.canned_rule import CannedRule


async def add(db: AsyncSession, rule: CannedRule) -> CannedRule:
    db.add(rule)
    await db.flush()
    return rule


async def list_for_vendor(db: AsyncSession, vendor_id: uuid.UUID) -> list[CannedRule]:
    result = await db.scalars(
        select(CannedRule)
        .where(CannedRule.vendor_id == vendor_id)
        .order_by(CannedRule.priority, CannedRule.id)
    )
    return list(result)
