"""How the API and the services ask Celery to do something later (plan section 8.3).

Every call only drops a small message on the broker. The database is the truth: if the broker is
down the call raises, the caller logs it, and the Celery Beat sweeper (section 8.5) picks the
work up again.
"""

import asyncio
from typing import Protocol

from celery import Celery

QUEUE_DISPATCH = "dispatch"
QUEUE_MAINTENANCE = "maintenance"
QUEUE_WEBHOOKS = "webhooks"


class Scheduler(Protocol):
    async def dispatch(self, vendor_id: str, delay: float = 0) -> None:
        """Run ``dispatch_next(vendor_id)`` now or after ``delay`` seconds."""

    async def check_lease(self, job_id: str, attempt: int, delay: float) -> None: ...

    async def deliver_webhook(self, delivery_id: int, delay: float = 0) -> None: ...

    async def run_virtual(self, job_id: str, attempt: int, phase: str, delay: float) -> None:
        """Step of the virtual plugin that answers test-key jobs."""


class CeleryScheduler:
    def __init__(self, app: Celery) -> None:
        self._app = app

    async def _send(self, name: str, args: list[object], queue: str, countdown: float = 0) -> None:
        def send() -> None:
            self._app.send_task(
                name, args=args, queue=queue, countdown=countdown if countdown > 0 else None
            )

        await asyncio.to_thread(send)

    async def dispatch(self, vendor_id: str, delay: float = 0) -> None:
        await self._send("gateway.dispatch_next", [vendor_id], QUEUE_DISPATCH, delay)

    async def check_lease(self, job_id: str, attempt: int, delay: float) -> None:
        await self._send("gateway.check_lease", [job_id, attempt], QUEUE_DISPATCH, delay)

    async def deliver_webhook(self, delivery_id: int, delay: float = 0) -> None:
        await self._send("gateway.deliver_webhook", [delivery_id], QUEUE_WEBHOOKS, delay)

    async def run_virtual(self, job_id: str, attempt: int, phase: str, delay: float) -> None:
        await self._send("gateway.run_virtual", [job_id, attempt, phase], QUEUE_DISPATCH, delay)
