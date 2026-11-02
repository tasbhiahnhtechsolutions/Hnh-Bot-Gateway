import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.vendor import Vendor, VendorStatus


async def add(db: AsyncSession, vendor: Vendor) -> Vendor:
    db.add(vendor)
    await db.flush()
    return vendor


async def get(db: AsyncSession, vendor_id: uuid.UUID) -> Vendor | None:
    return await db.get(Vendor, vendor_id)


async def list_all(db: AsyncSession) -> list[Vendor]:
    result = await db.scalars(select(Vendor).order_by(Vendor.created_at))
    return list(result)


async def get_active_by_phone(db: AsyncSession, phone_e164: str) -> Vendor | None:
    return await db.scalar(
        select(Vendor).where(
            Vendor.phone_e164 == phone_e164, Vendor.status == VendorStatus.ACTIVE.value
        )
    )


async def get_by_business_name(db: AsyncSession, business_name: str) -> Vendor | None:
    return await db.scalar(select(Vendor).where(Vendor.business_name == business_name))
