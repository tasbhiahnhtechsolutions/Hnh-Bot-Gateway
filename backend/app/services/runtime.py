"""What the outbound services need besides a database session."""

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from redis.asyncio import Redis

from app.core.config import Settings
from app.core.security import SecretBox
from app.services.scheduling import Scheduler


class Emitter(Protocol):
    """Sends a job to the plugin of one vendor (Socket.IO room ``vendor:<id>``)."""

    async def emit_job(self, vendor_id: str, payload: dict[str, Any]) -> None: ...


@dataclass(slots=True)
class Runtime:
    settings: Settings
    redis: Redis
    scheduler: Scheduler
    emitter: Emitter
    box: SecretBox
    clock: Callable[[], float] = field(default=time.time)  # tests move time by replacing this
