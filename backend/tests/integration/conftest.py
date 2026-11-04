"""Real Postgres and Redis via testcontainers (needs Docker). Migrations build the schema."""

import asyncio
import json
import os
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from dataclasses import dataclass, field, replace
from typing import Any

import httpx
import pytest
import uvicorn
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from testcontainers.community.postgres import PostgresContainer
from testcontainers.community.redis import RedisContainer

from app.core.config import AppEnv, Settings
from app.core.security import SecretBox
from app.main import create_app
from app.realtime.emitter import ServerEmitter
from app.schemas.vendor import VendorCreate
from app.services.inbound import InboundDeps
from app.services.runtime import Runtime
from app.services.vendors import CreatedVendor, create_vendor
from tests.integration.live_support import Live, free_port
from tests.integration.outbound_support import (
    FakeClock,
    InlineScheduler,
    Outbound,
    RecordingEmitter,
)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if "tests/integration" in str(item.fspath):
            item.add_marker(pytest.mark.integration)


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    with PostgresContainer("postgres:16-alpine", driver="asyncpg") as pg:
        yield pg.get_connection_url()


@pytest.fixture(scope="session")
def redis_url() -> Iterator[str]:
    with RedisContainer("redis:7-alpine") as redis:
        host = redis.get_container_host_ip()
        port = redis.get_exposed_port(6379)
        yield f"redis://{host}:{port}/0"


def alembic_config(url: str) -> Config:
    os.environ["DATABASE_URL"] = url
    return Config("alembic.ini")


@pytest.fixture(scope="session")
def migrated_url(postgres_url: str) -> str:
    command.upgrade(alembic_config(postgres_url), "head")
    return postgres_url


@pytest.fixture(scope="session")
async def engine(migrated_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(migrated_url)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """A session whose work is rolled back, so tests do not see each other's rows."""
    async with engine.connect() as conn:
        trans = await conn.begin()
        factory = async_sessionmaker(
            bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
        )
        async with factory() as session:
            yield session
        await trans.rollback()


@pytest.fixture
async def clean_db(engine: AsyncEngine) -> AsyncIterator[None]:
    """For tests that really commit (services, CLI). Empties the tables before and after."""

    async def wipe() -> None:
        async with engine.begin() as conn:
            await conn.execute(text("TRUNCATE vendors, admins CASCADE"))

    await wipe()
    yield
    await wipe()


# --- full API with real Postgres + Redis and a fake vendor-bot network -----------------------


class FakeBot:
    """Stands in for every vendor bot on the internet. Records what was sent to it."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.handler: Callable[[httpx.Request], httpx.Response] = lambda _r: httpx.Response(
            200, json={"replies": ["bot says hi"]}
        )
        self.transport = httpx.MockTransport(self._handle)

    async def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handler(request)


@dataclass
class Api:
    client: httpx.AsyncClient
    app: FastAPI
    settings: Settings
    box: SecretBox
    bot: FakeBot
    # API key -> the vendor's WhatsApp number. post_reply sends it as X-WA-Number like the real
    # plugin does, because the gateway rejects a missing or wrong number by default.
    numbers: dict[str, str] = field(default_factory=dict)
    out: Outbound | None = None

    @property
    def ob(self) -> Outbound:
        assert self.out is not None
        return self.out

    async def vendor(self, **fields: object) -> CreatedVendor:
        values: dict[str, object] = {
            "business_name": f"Vendor {uuid.uuid4().hex[:6]}",
            "whatsapp_number": str(923000000000 + uuid.uuid4().int % 10**9),
            "email": "v@example.com",
            "bot_base_url": "http://bot.test/ai-reply",
        }
        values.update(fields)
        async with self.app.state.session_factory() as db:
            created = await create_vendor(
                db,
                VendorCreate(**values),
                box=self.box,
                allow_private_bot_urls=True,
            )
            await db.commit()
        self.numbers[created.secrets.live_key] = created.vendor.phone_e164
        self.numbers[created.secrets.test_key] = created.vendor.phone_e164
        return created

    async def post_reply(
        self,
        key: str,
        body: dict[str, Any] | bytes | None = None,
        headers: dict[str, str] | None = None,
        send_wa_number: bool = True,
    ) -> httpx.Response:
        content = body if isinstance(body, bytes) else json.dumps(body or REQUEST_BODY).encode()
        all_headers = {"X-API-Key": key, "Content-Type": "application/json"}
        if send_wa_number and key in self.numbers:
            all_headers["X-WA-Number"] = self.numbers[key]
        all_headers.update(headers or {})
        return await self.client.post("/v1/ai-reply", content=content, headers=all_headers)


REQUEST_BODY: dict[str, Any] = {
    "chatId": "923001112222@c.us",
    "chatTitle": "Sara Khan",
    "senderName": "Sara Khan",
    "senderNumber": "+923001112222",
    "isSaved": False,
    "incomingMessages": [{"text": "Red lehenga available hai?", "timestamp": "19:30:12"}],
    "chatHistory": [],
}

MakeApi = Callable[..., Awaitable[Api]]


@pytest.fixture
async def make_api(migrated_url: str, redis_url: str, clean_db: None) -> AsyncIterator[MakeApi]:
    created: list[Api] = []

    async def factory(**overrides: object) -> Api:
        values: dict[str, object] = {
            "app_env": AppEnv.TEST,
            "database_url": migrated_url,
            "redis_url": redis_url,
            "allow_private_bot_urls": True,
            "enable_mock_bot": True,
            "bot_timeout_seconds": 5.0,
        }
        values.update(overrides)
        settings = Settings(**values)  # type: ignore[arg-type]
        app = create_app(settings)
        await app.state.redis.flushdb()
        bot = FakeBot()
        box = SecretBox(settings.secret_encryption_key.get_secret_value())
        app.state.inbound_deps = InboundDeps(
            settings=settings, box=box, bot_transport=bot.transport
        )
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        api = Api(client=client, app=app, settings=settings, box=box, bot=bot)
        # Outbound: no Celery broker, no real socket. The test plays worker and plugin.
        scheduler, emitter, clock = InlineScheduler(), RecordingEmitter(), FakeClock()
        runtime = Runtime(
            settings=settings,
            redis=app.state.redis,
            scheduler=scheduler,
            emitter=emitter,
            box=box,
            clock=clock,
        )
        app.state.runtime = runtime
        api.out = Outbound(api, runtime, scheduler, emitter, clock)
        created.append(api)
        return api

    yield factory

    for api in created:
        await api.client.aclose()
        await api.app.state.redis.aclose()
        await api.app.state.engine.dispose()


@pytest.fixture
async def live(make_api: MakeApi) -> AsyncIterator[Live]:
    """The app served over real TCP, with jobs delivered through the real Socket.IO hub. Celery
    stays a recorder unless a test swaps in a real scheduler."""
    api = await make_api()
    hub = api.app.state.hub
    api.ob.rt = replace(api.ob.rt, emitter=ServerEmitter(hub.sio))
    api.app.state.runtime = api.ob.rt
    port = free_port()
    server = uvicorn.Server(
        uvicorn.Config(api.app, host="127.0.0.1", port=port, log_level="error", lifespan="off")
    )
    task = asyncio.create_task(server.serve())
    while not server.started:  # noqa: ASYNC110
        await asyncio.sleep(0.02)
    state = Live(api, f"http://127.0.0.1:{port}")
    yield state
    for client in state.clients:
        if client.connected:
            await client.disconnect()
    server.should_exit = True
    await task
