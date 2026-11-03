"""A fake plugin and a real server for tests that need an actual Socket.IO connection."""

import asyncio
import socket
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pytest
import socketio

from app.services.vendors import CreatedVendor

if TYPE_CHECKING:
    from tests.integration.conftest import Api


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class Refused(Exception):
    """The hub said no; the message is its refusal code."""


@dataclass
class Plugin:
    """A fake plugin: remembers every job it receives."""

    client: socketio.AsyncClient
    jobs: list[dict[str, Any]] = field(default_factory=list)
    kicked: asyncio.Event = field(default_factory=asyncio.Event)
    refusal: list[str] = field(default_factory=list)

    async def wait_for_jobs(self, count: int, timeout_seconds: float = 5.0) -> None:
        async with asyncio.timeout(timeout_seconds):
            while len(self.jobs) < count:  # noqa: ASYNC110
                await asyncio.sleep(0.02)

    async def ack(self, job_id: str, attempt: int) -> dict[str, Any]:
        reply = await self.client.call("job_ack", {"jobId": job_id, "attempt": attempt}, timeout=5)
        return dict(reply)

    async def result(self, job_id: str, attempt: int, status: str, **extra: Any) -> dict[str, Any]:
        body = {"jobId": job_id, "attempt": attempt, "status": status, **extra}
        return dict(await self.client.call("job_result", body, timeout=5))

    async def heartbeat(self, wa_state: str = "connected") -> dict[str, Any]:
        return dict(await self.client.call("heartbeat", {"waState": wa_state}, timeout=5))


@dataclass
class Live:
    api: "Api"
    url: str
    clients: list[socketio.AsyncClient] = field(default_factory=list)

    async def connect(
        self,
        created: CreatedVendor,
        *,
        key: str | None = None,
        device: str = "device-0001",
        **auth: Any,
    ) -> Plugin:
        client = socketio.AsyncClient(reconnection=False)
        plugin = Plugin(client)

        @client.on("job")  # type: ignore[untyped-decorator]
        async def _job(data: dict[str, Any]) -> None:
            plugin.jobs.append(data)

        @client.on("connect_error")  # type: ignore[untyped-decorator]
        async def _refused(data: Any) -> None:
            plugin.refusal.append(str(data))

        @client.on("disconnect")  # type: ignore[untyped-decorator]
        async def _gone() -> None:
            plugin.kicked.set()

        payload = {
            "apiKey": key or created.secrets.live_key,
            "deviceId": device,
            "waNumber": created.vendor.phone_e164,
            "version": "0.2.0",
            **auth,
        }
        try:
            await client.connect(self.url, auth=payload, socketio_path="/socket.io", wait_timeout=5)
        except socketio.exceptions.ConnectionError as exc:
            raise Refused(" ".join(plugin.refusal) or str(exc)) from exc
        self.clients.append(client)
        return plugin

    async def refused(self, created: CreatedVendor, **kwargs: Any) -> str:
        with pytest.raises(Refused) as info:
            await self.connect(created, **kwargs)
        return str(info.value)
