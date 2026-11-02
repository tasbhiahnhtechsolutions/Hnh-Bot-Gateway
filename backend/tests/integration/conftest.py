"""Real Postgres and Redis via testcontainers (needs Docker). Migrations build the schema."""

import os
from collections.abc import AsyncIterator, Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from testcontainers.community.postgres import PostgresContainer
from testcontainers.community.redis import RedisContainer


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
