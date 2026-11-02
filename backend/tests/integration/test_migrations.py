from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.base import Base
from tests.integration.conftest import alembic_config


def _diff(conn: Connection) -> list[object]:
    ctx = MigrationContext.configure(
        conn, opts={"compare_type": True, "compare_server_default": True}
    )
    return list(compare_metadata(ctx, Base.metadata))


async def test_models_match_migrated_schema(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        assert await conn.run_sync(_diff) == []


async def test_expected_tables_exist(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        tables = await conn.run_sync(lambda c: set(inspect(c).get_table_names()))
    assert {"vendors", "api_keys", "canned_rules", "request_logs", "alembic_version"} <= tables


def test_downgrade_then_upgrade_is_clean(postgres_url: str, migrated_url: str) -> None:
    cfg = alembic_config(postgres_url)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
