"""Issuing and rotating API keys. Callers own the transaction (commit)."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.core.security import KeyKind, generate_api_key
from app.models import ApiKey
from app.repositories import api_keys, vendors

_PREFIX_ATTEMPTS = 5


async def issue_key(db: AsyncSession, vendor_id: uuid.UUID, kind: KeyKind) -> str:
    """Create a key and return the full value. This is the only time it exists in plain text."""
    for _ in range(_PREFIX_ATTEMPTS):
        generated = generate_api_key(kind)
        if await api_keys.prefix_exists(db, generated.prefix):
            continue  # 8 hex chars collide rarely; the unique constraint is the backstop
        await api_keys.add(
            db,
            ApiKey(
                vendor_id=vendor_id,
                kind=kind.value,
                key_prefix=generated.prefix,
                key_hash=generated.key_hash,
            ),
        )
        return generated.full_key
    raise RuntimeError("could not find an unused API key prefix")


async def rotate_key(db: AsyncSession, vendor_id: uuid.UUID, kind: KeyKind) -> str:
    """Revoke the vendor's active keys of this kind and issue a new one."""
    if await vendors.get(db, vendor_id) is None:
        raise ApiError(404, "VENDOR_NOT_FOUND", "no vendor with this id")
    await api_keys.revoke_all_active(db, vendor_id, kind.value)
    return await issue_key(db, vendor_id, kind)
