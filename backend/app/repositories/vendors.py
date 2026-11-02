import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.vendor import Vendor


async def add(db: AsyncSession, vendor: Vendor) -> Vendor:
    db.add(vendor)
    await db.flush()
    return vendor


async def get(db: AsyncSession, vendor_id: uuid.UUID) -> Vendor | None:
    return await db.get(Vendor, vendor_id)


async def list_all(db: AsyncSession) -> list[Vendor]:
    result = await db.scalars(select(Vendor).order_by(Vendor.created_at))
    return list(result)
