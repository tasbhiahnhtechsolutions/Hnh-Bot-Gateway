"""Who is connected: one active session per key (plan section 8.4).

presence:vendor:{id}   "<sid>|<deviceId>"  NX EX 45, refreshed by every heartbeat
wastate:vendor:{id}    last waState the plugin reported, same lifetime
"""

import uuid
from dataclasses import dataclass

from redis.asyncio import Redis

PRESENCE_TTL_SECONDS = 45
WA_CONNECTED = "connected"
WA_STATES = frozenset({"connected", "connecting", "offline", "logged_out", "not_loaded"})

_CLAIM = """
local current = redis.call('GET', KEYS[1])
if not current then
    redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[3])
    return {1, ''}
end
local sep = string.find(current, '|', 1, true)
local cur_device = string.sub(current, sep + 1)
if cur_device == ARGV[2] then
    redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[3])
    return {1, string.sub(current, 1, sep - 1)}
end
return {0, ''}
"""

_RELEASE = """
local current = redis.call('GET', KEYS[1])
if current and string.sub(current, 1, string.len(ARGV[1]) + 1) == ARGV[1] .. '|' then
    redis.call('DEL', KEYS[1])
    redis.call('DEL', KEYS[2])
    return 1
end
return 0
"""


def presence_key(vendor_id: uuid.UUID | str) -> str:
    return f"presence:vendor:{vendor_id}"


def wastate_key(vendor_id: uuid.UUID | str) -> str:
    return f"wastate:vendor:{vendor_id}"


@dataclass(frozen=True, slots=True)
class ClaimResult:
    ok: bool
    replaced_sid: str | None = None  # the old socket of the same device (take-over)


async def claim(redis: Redis, vendor_id: uuid.UUID | str, sid: str, device_id: str) -> ClaimResult:
    """Take the line. Free, or held by the same device (a reconnect that the server has not
    noticed yet) means yes. Held by another live device means no."""
    result = await redis.eval(  # type: ignore[misc]
        _CLAIM,
        1,
        presence_key(vendor_id),
        f"{sid}|{device_id}",
        device_id,
        str(PRESENCE_TTL_SECONDS),
    )
    ok, old_sid = int(result[0]), result[1]
    if isinstance(old_sid, bytes):
        old_sid = old_sid.decode()
    return ClaimResult(ok=bool(ok), replaced_sid=old_sid or None)


async def refresh(redis: Redis, vendor_id: uuid.UUID | str, sid: str, wa_state: str | None) -> bool:
    """Heartbeat. False when this socket no longer holds the line."""
    raw = await redis.get(presence_key(vendor_id))
    if raw is None or not raw.decode().startswith(f"{sid}|"):
        return False
    await redis.expire(presence_key(vendor_id), PRESENCE_TTL_SECONDS)
    if wa_state in WA_STATES:
        await redis.set(wastate_key(vendor_id), wa_state, ex=PRESENCE_TTL_SECONDS)
    return True


async def release(redis: Redis, vendor_id: uuid.UUID | str, sid: str) -> bool:
    return bool(
        await redis.eval(_RELEASE, 2, presence_key(vendor_id), wastate_key(vendor_id), sid)  # type: ignore[misc]
    )


async def holder_sid(redis: Redis, vendor_id: uuid.UUID | str) -> str | None:
    raw = await redis.get(presence_key(vendor_id))
    return raw.decode().split("|", 1)[0] if raw else None


async def wa_state(redis: Redis, vendor_id: uuid.UUID | str) -> str | None:
    raw = await redis.get(wastate_key(vendor_id))
    return raw.decode() if raw else None


async def is_online(redis: Redis, vendor_id: uuid.UUID | str) -> bool:
    return bool(await redis.exists(presence_key(vendor_id)))


async def is_online_and_wa_connected(redis: Redis, vendor_id: uuid.UUID | str) -> bool:
    return await is_online(redis, vendor_id) and await wa_state(redis, vendor_id) == WA_CONNECTED
