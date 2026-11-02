"""Send a job from any process to the plugin, through Redis (plan section 8.3).

The worker is a different process from the one that holds the plugin's socket, so it cannot talk
to the socket directly. python-socketio's Redis manager (write only here) publishes the event
and the API process that owns the socket delivers it.
"""

from typing import Any

import socketio

ROOM_PREFIX = "vendor:"


def vendor_room(vendor_id: str) -> str:
    return f"{ROOM_PREFIX}{vendor_id}"


class RedisEmitter:
    """Used by Celery workers. One short-lived instance per task."""

    def __init__(self, redis_url: str) -> None:
        self._manager = socketio.AsyncRedisManager(redis_url, write_only=True)

    async def emit_job(self, vendor_id: str, payload: dict[str, Any]) -> None:
        await self._manager.emit("job", payload, room=vendor_room(vendor_id), namespace="/")

    async def close(self) -> None:
        client = getattr(self._manager, "redis", None)
        if client is not None:
            await client.aclose()


class ServerEmitter:
    """Used inside the API process, where the Socket.IO server lives."""

    def __init__(self, server: socketio.AsyncServer) -> None:
        self._server = server

    async def emit_job(self, vendor_id: str, payload: dict[str, Any]) -> None:
        await self._server.emit("job", payload, room=vendor_room(vendor_id))
