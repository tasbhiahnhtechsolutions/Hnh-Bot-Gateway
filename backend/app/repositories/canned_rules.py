import uuid

from sqlalchemy import delete, select
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


async def delete_for_vendor(db: AsyncSession, vendor_id: uuid.UUID, rule_id: uuid.UUID) -> bool:
    result = await db.execute(
        delete(CannedRule)
        .where(CannedRule.id == rule_id, CannedRule.vendor_id == vendor_id)
        .returning(CannedRule.id)
    )
    return result.scalar_one_or_none() is not None
