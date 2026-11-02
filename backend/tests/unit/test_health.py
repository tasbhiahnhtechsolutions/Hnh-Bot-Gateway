from typing import Any

import httpx
import pytest

from app.core.config import Settings
from app.main import create_app


class FakeConn:
    async def __aenter__(self) -> "FakeConn":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def execute(self, *_: object) -> None:
        return None


class FakeEngine:
    def __init__(self, healthy: bool) -> None:
        self.healthy = healthy

    def connect(self) -> FakeConn:
        if not self.healthy:
            raise ConnectionError("postgresql://user:hunter2@db/secret is down")
        return FakeConn()


class FakeRedis:
    def __init__(self, healthy: bool) -> None:
        self.healthy = healthy

    async def ping(self) -> bool:
        if not self.healthy:
            raise ConnectionError("redis://:hunter2@cache is down")
        return True


def make_client(db_ok: bool, redis_ok: bool) -> httpx.AsyncClient:
    app = create_app(Settings())
    app.state.engine = FakeEngine(db_ok)
    app.state.redis = FakeRedis(redis_ok)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_health_is_always_ok() -> None:
    async with make_client(db_ok=False, redis_ok=False) as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_ready_ok_when_both_up() -> None:
    async with make_client(True, True) as client:
        response = await client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"database": "ok", "redis": "ok"}}


@pytest.mark.parametrize(
    ("db_ok", "redis_ok", "expected"),
    [
        (False, True, {"database": "fail", "redis": "ok"}),
        (True, False, {"database": "ok", "redis": "fail"}),
        (False, False, {"database": "fail", "redis": "fail"}),
    ],
)
async def test_ready_503_and_no_leak_when_a_dependency_is_down(
    db_ok: bool, redis_ok: bool, expected: dict[str, Any]
) -> None:
    async with make_client(db_ok, redis_ok) as client:
        response = await client.get("/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable", "checks": expected}
    assert "hunter2" not in response.text
