import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.api_key import ApiKey, ApiKeyStatus


async def add(db: AsyncSession, key: ApiKey) -> ApiKey:
    db.add(key)
    await db.flush()
    return key


async def prefix_exists(db: AsyncSession, prefix: str) -> bool:
    found = await db.scalar(select(ApiKey.id).where(ApiKey.key_prefix == prefix))
    return found is not None


async def find_active_by_prefix(db: AsyncSession, prefix: str) -> ApiKey | None:
    return await db.scalar(
        select(ApiKey).where(
            ApiKey.key_prefix == prefix, ApiKey.status == ApiKeyStatus.ACTIVE.value
        )
    )


async def list_for_vendor(db: AsyncSession, vendor_id: uuid.UUID) -> list[ApiKey]:
    result = await db.scalars(
        select(ApiKey).where(ApiKey.vendor_id == vendor_id).order_by(ApiKey.created_at)
    )
    return list(result)


async def revoke(db: AsyncSession, key_id: uuid.UUID) -> bool:
    """Revoke an active key. Returns False if it was not active (or does not exist)."""
    result = await db.execute(
        update(ApiKey)
        .where(ApiKey.id == key_id, ApiKey.status == ApiKeyStatus.ACTIVE.value)
        .values(status=ApiKeyStatus.REVOKED.value, revoked_at=datetime.now(UTC))
        .returning(ApiKey.id)
    )
    return result.scalar_one_or_none() is not None


async def touch_last_used(db: AsyncSession, key_id: uuid.UUID) -> None:
    await db.execute(
        update(ApiKey).where(ApiKey.id == key_id).values(last_used_at=datetime.now(UTC))
    )
