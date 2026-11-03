"""Live view of a vendor's plugin connection for the admin panel."""

import uuid
from dataclasses import dataclass
from datetime import datetime

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories import outbound as repo
from app.services import presence


@dataclass(frozen=True, slots=True)
class DeviceView:
    device_id: str
    wa_number: str | None
    plugin_version: str | None
    last_seen_at: datetime
    online: bool
    wa_state: str | None  # only known for the device that holds the line
    heartbeat_age_seconds: int | None  # seconds since its last heartbeat (online device only)


async def live_devices(db: AsyncSession, redis: Redis, vendor_id: uuid.UUID) -> list[DeviceView]:
    holder = await redis.get(presence.presence_key(vendor_id))
    online_device = holder.decode().split("|", 1)[1] if holder and b"|" in holder else None
    wa_state = await presence.wa_state(redis, vendor_id) if online_device else None
    age: int | None = None
    if online_device:
        ttl = await redis.ttl(presence.presence_key(vendor_id))
        age = max(0, presence.PRESENCE_TTL_SECONDS - ttl) if ttl >= 0 else None
    return [
        DeviceView(
            device_id=d.device_id,
            wa_number=d.wa_number,
            plugin_version=d.plugin_version,
            last_seen_at=d.last_seen_at,
            online=d.device_id == online_device,
            wa_state=wa_state if d.device_id == online_device else None,
            heartbeat_age_seconds=age if d.device_id == online_device else None,
        )
        for d in await repo.list_devices(db, vendor_id)
    ]
