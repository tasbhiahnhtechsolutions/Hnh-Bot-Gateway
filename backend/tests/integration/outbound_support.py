"""Test doubles for the outbound pipeline: a scheduler that runs Celery's jobs inline, an emitter
that records what a plugin would receive, a clock tests can move, and an ``Outbound`` helper that
plays the role of a plugin."""

import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx
from sqlalchemy import select

from app.models import Vendor
from app.models.outbound import OutboundJob, WebhookDelivery
from app.services import dispatch, presence, sweepers, virtual_plugin, webhooks
from app.services.job_results import Applied, apply_result, apply_started
from app.services.runtime import Runtime

if TYPE_CHECKING:
    from tests.integration.conftest import Api


class FakeClock:
    def __init__(self, now: float | None = None) -> None:
        self.now = time.time() if now is None else now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds

    @property
    def dt(self) -> datetime:
        return datetime.fromtimestamp(self.now, UTC)


@dataclass
class RecordingEmitter:
    sent: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    fail: bool = False

    async def emit_job(self, vendor_id: str, payload: dict[str, Any]) -> None:
        if self.fail:
            raise ConnectionError("hub down")
        self.sent.append((vendor_id, payload))

    def jobs_for(self, vendor_id: uuid.UUID | str) -> list[dict[str, Any]]:
        return [p for v, p in self.sent if v == str(vendor_id)]


@dataclass
class Call:
    name: str
    args: tuple[Any, ...]
    delay: float


class InlineScheduler:
    """Collects what the services would hand to Celery. ``drain`` plays the worker."""

    def __init__(self) -> None:
        self.calls: list[Call] = []
        self.broken = False

    def _add(self, name: str, args: tuple[Any, ...], delay: float) -> None:
        if self.broken:
            raise ConnectionError("broker down")
        self.calls.append(Call(name, args, delay))

    async def dispatch(self, vendor_id: str, delay: float = 0) -> None:
        self._add("dispatch", (vendor_id,), delay)

    async def check_lease(self, job_id: str, attempt: int, delay: float) -> None:
        self._add("check_lease", (job_id, attempt), delay)

    async def deliver_webhook(self, delivery_id: int, delay: float = 0) -> None:
        self._add("deliver_webhook", (delivery_id,), delay)

    async def run_virtual(self, job_id: str, attempt: int, phase: str, delay: float) -> None:
        self._add("run_virtual", (job_id, attempt, phase), delay)

    def names(self) -> list[str]:
        return [c.name for c in self.calls]

    def count(self, name: str) -> int:
        return sum(1 for c in self.calls if c.name == name)


@dataclass
class Outbound:
    """Everything a test needs to drive the outbound pipeline by hand."""

    api: "Api"
    rt: Runtime
    scheduler: InlineScheduler
    emitter: RecordingEmitter
    clock: FakeClock
    hook_transport: httpx.AsyncBaseTransport | None = None

    async def post(
        self,
        key: str,
        number: str = "03001234567",
        message: str = "Aap ka OTP 482913 hai.",
        type: str | None = None,
        ttl: int | None = None,
        idem: str | None = None,
        **extra: object,
    ) -> httpx.Response:
        body: dict[str, object] = {"number": number, "message": message, **extra}
        if type is not None:
            body["type"] = type
        if ttl is not None:
            body["ttlSeconds"] = ttl
        headers = {"X-API-Key": key}
        self.clock.advance(0.001)  # jobs of one test must not share a creation time
        if idem is not None:
            headers["Idempotency-Key"] = idem
        return await self.api.client.post("/v1/message-to-customer", json=body, headers=headers)

    async def row(self, job_id: str) -> OutboundJob:
        async with self.api.app.state.session_factory() as db:
            job: OutboundJob | None = await db.scalar(
                select(OutboundJob).where(OutboundJob.id == job_id)
            )
            assert job is not None
            return job

    async def status(self, job_id: str) -> str:
        return (await self.row(job_id)).status

    async def online(
        self, vendor: Vendor, wa_state: str = "connected", device: str = "dev-0001"
    ) -> str:
        sid = f"sid-{uuid.uuid4().hex[:8]}"
        claim = await presence.claim(self.rt.redis, vendor.id, sid, device)
        assert claim.ok
        await presence.refresh(self.rt.redis, vendor.id, sid, wa_state)
        return sid

    async def dispatch(self, vendor: Vendor | uuid.UUID) -> dict[str, str]:
        vendor_id = vendor if isinstance(vendor, uuid.UUID) else vendor.id
        async with self.api.app.state.session_factory() as db:
            return await dispatch.dispatch_next(db, self.rt, vendor_id)

    async def started(self, vendor: Vendor, job_id: str, attempt: int) -> Applied:
        async with self.api.app.state.session_factory() as db:
            return await apply_started(db, vendor.id, job_id, attempt, self.clock.dt)

    async def result(
        self,
        vendor: Vendor,
        job_id: str,
        attempt: int,
        status: str,
        *,
        wa_ack: int | None = None,
        error_code: str | None = None,
    ) -> Applied:
        async with self.api.app.state.session_factory() as db:
            fresh = await db.get(Vendor, vendor.id)
            assert fresh is not None
            return await apply_result(
                db, self.rt, fresh, job_id, attempt, status, wa_ack=wa_ack, error_code=error_code
            )

    async def drain(self, *, delayed: bool = False, limit: int = 200) -> None:
        """Run the queued Celery calls. ``delayed`` also runs those booked for later (as if the
        time had passed). New calls made on the way are run too."""
        steps = 0
        while steps < limit:
            runnable = [c for c in self.scheduler.calls if delayed or c.delay <= 0]
            if not runnable:
                return
            call = runnable[0]
            self.scheduler.calls.remove(call)
            steps += 1
            async with self.api.app.state.session_factory() as db:
                if call.name == "dispatch":
                    await dispatch.dispatch_next(db, self.rt, uuid.UUID(call.args[0]))
                elif call.name == "check_lease":
                    await sweepers.check_lease(
                        db, self.rt, call.args[0], call.args[1], now=self.clock.dt
                    )
                elif call.name == "run_virtual":
                    await virtual_plugin.run_step(
                        db, self.rt, call.args[0], call.args[1], call.args[2]
                    )
                elif call.name == "deliver_webhook":
                    delay = await webhooks.attempt_delivery(
                        db,
                        call.args[0],
                        settings=self.rt.settings,
                        box=self.rt.box,
                        transport=self.hook_transport,
                        now=self.clock.dt,
                    )
                    if delay is not None:
                        await self.scheduler.deliver_webhook(call.args[0], delay)
        raise AssertionError("drain did not settle")

    async def deliveries(self, vendor_id: uuid.UUID) -> list[WebhookDelivery]:
        async with self.api.app.state.session_factory() as db:
            rows = await db.scalars(
                select(WebhookDelivery)
                .where(WebhookDelivery.vendor_id == vendor_id)
                .order_by(WebhookDelivery.id)
            )
            return list(rows.all())
