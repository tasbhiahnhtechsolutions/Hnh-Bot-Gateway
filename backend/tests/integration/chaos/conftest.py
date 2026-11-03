"""Toxiproxy sits between the app and Redis so a test can cut or slow the connection."""

from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass

import httpx
import pytest
from testcontainers.core.container import DockerContainer
from testcontainers.core.waiting_utils import wait_for_logs

PROXY_PORT = 8666


@dataclass
class Toxiproxy:
    api: httpx.Client
    host: str
    redis_port: int  # the host port that leads to Redis through the proxy

    def set_enabled(self, enabled: bool) -> None:
        response = self.api.post("/proxies/redis", json={"enabled": enabled})
        response.raise_for_status()

    def add_toxic(self, kind: str, **attributes: float) -> None:
        response = self.api.post(
            "/proxies/redis/toxics", json={"type": kind, "attributes": attributes}
        )
        response.raise_for_status()

    def clear_toxics(self) -> None:
        for toxic in self.api.get("/proxies/redis/toxics").json():
            self.api.delete(f"/proxies/redis/toxics/{toxic['name']}").raise_for_status()

    @property
    def redis_url(self) -> str:
        return f"redis://{self.host}:{self.redis_port}/0"


@pytest.fixture(scope="session")
def toxiproxy(redis_url: str) -> Iterator[Toxiproxy]:
    redis_port = int(redis_url.rsplit(":", 1)[1].split("/")[0])
    container = (
        DockerContainer("ghcr.io/shopify/toxiproxy:2.9.0")
        .with_exposed_ports(8474, PROXY_PORT)
        .with_kwargs(extra_hosts={"host.docker.internal": "host-gateway"})
    )
    with container:
        wait_for_logs(container, "Starting Toxiproxy", timeout=30)
        host = container.get_container_host_ip()
        control = httpx.Client(base_url=f"http://{host}:{container.get_exposed_port(8474)}")
        control.post(
            "/proxies",
            json={
                "name": "redis",
                "listen": f"0.0.0.0:{PROXY_PORT}",
                "upstream": f"host.docker.internal:{redis_port}",
            },
        ).raise_for_status()
        yield Toxiproxy(control, host, int(container.get_exposed_port(PROXY_PORT)))
        control.close()


@pytest.fixture
async def healthy(toxiproxy: Toxiproxy) -> AsyncIterator[Toxiproxy]:
    toxiproxy.set_enabled(True)
    toxiproxy.clear_toxics()
    yield toxiproxy
    toxiproxy.set_enabled(True)
    toxiproxy.clear_toxics()
