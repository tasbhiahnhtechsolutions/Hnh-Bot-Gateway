import re
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.cli import run
from app.core.config import AppEnv, Settings
from app.core.security import KeyKind, parse_api_key
from app.repositories import api_keys, vendors

pytestmark = pytest.mark.usefixtures("clean_db")


@pytest.fixture
def settings(migrated_url: str) -> Settings:
    return Settings(
        app_env=AppEnv.TEST,
        database_url=migrated_url,
        allow_private_bot_urls=True,
    )


def create_args(**overrides: str) -> list[str]:
    values = {
        "--name": "Ali Boutique",
        "--number": "+92 300 1234567",
        "--email": "ali@example.com",
        "--bot-url": "http://localhost:9000",
    }
    values.update(overrides)
    return [x for pair in values.items() for x in pair]


async def one_session(engine: AsyncEngine) -> AsyncSession:
    return async_sessionmaker(engine, expire_on_commit=False)()


def grab(label: str, out: str) -> str:
    match = re.search(rf"{label}:\s+(\S+)", out)
    assert match, out
    return match.group(1)


async def test_create_vendor_prints_secrets_once_and_persists(
    settings: Settings, engine: AsyncEngine, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await run(["create-vendor", *create_args()], settings) == 0
    out = capsys.readouterr().out
    live = grab("LIVE key", out)
    assert parse_api_key(live) is not None
    assert grab("Bot secret", out).startswith("whsec_")

    async with await one_session(engine) as db:
        vendor = await vendors.get_active_by_phone(db, "923001234567")
        assert vendor is not None
        assert {k.kind for k in await api_keys.list_for_vendor(db, vendor.id)} == {"live", "test"}


async def test_create_vendor_errors_exit_nonzero_and_store_nothing(
    settings: Settings, engine: AsyncEngine, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await run(["create-vendor", *create_args(**{"--number": "12"})], settings) == 2
    assert "whatsapp_number" in capsys.readouterr().err

    assert await run(["create-vendor", *create_args()], settings) == 0
    capsys.readouterr()
    assert await run(["create-vendor", *create_args(**{"--name": "Twin"})], settings) == 1
    assert "VENDOR_NUMBER_IN_USE" in capsys.readouterr().err

    async with await one_session(engine) as db:
        assert len(await vendors.list_all(db)) == 1


async def test_list_vendors_never_prints_secrets(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    await run(["create-vendor", *create_args()], settings)
    created_out = capsys.readouterr().out
    await run(["list-vendors"], settings)
    out = capsys.readouterr().out
    assert "Ali Boutique" in out
    assert "923001234567" in out
    assert grab("LIVE key", created_out) not in out
    assert "whsec_" not in out


async def test_rotate_key_via_cli(
    settings: Settings, engine: AsyncEngine, capsys: pytest.CaptureFixture[str]
) -> None:
    await run(["create-vendor", *create_args()], settings)
    old_live = grab("LIVE key", capsys.readouterr().out)
    async with await one_session(engine) as db:
        vendor = await vendors.get_active_by_phone(db, "923001234567")
        assert vendor is not None
        vendor_id = vendor.id

    assert await run(["rotate-key", str(vendor_id), "--kind", "live"], settings) == 0
    new_live = capsys.readouterr().out.split()[-1]
    assert parse_api_key(new_live) is not None
    assert new_live != old_live

    old_parsed = parse_api_key(old_live)
    new_parsed = parse_api_key(new_live)
    assert old_parsed is not None
    assert new_parsed is not None
    async with await one_session(engine) as db:
        assert await api_keys.find_active_by_prefix(db, old_parsed.prefix) is None
        assert await api_keys.find_active_by_prefix(db, new_parsed.prefix) is not None

    assert await run(["rotate-key", str(uuid.uuid4())], settings) == 1
    assert "VENDOR_NOT_FOUND" in capsys.readouterr().err


async def test_seed_creates_three_demo_vendors_and_is_repeatable(
    settings: Settings, engine: AsyncEngine, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await run(["seed"], settings) == 0
    first = capsys.readouterr().out
    assert first.count("Vendor created:") == 3

    assert await run(["seed"], settings) == 0
    second = capsys.readouterr().out
    assert "Vendor created" not in second
    assert second.count("already exists") == 3

    async with await one_session(engine) as db:
        modes = {v.business_name: v.inbound_mode for v in await vendors.list_all(db)}
    assert modes == {
        "demo-canned": "canned",
        "demo-echo": "echo",
        "demo-mockbot": "mock_bot",
    }
    live_key = KeyKind.LIVE.value
    assert f"wab_{live_key}_" in first


async def test_seed_refuses_where_private_urls_are_not_allowed(
    migrated_url: str, engine: AsyncEngine, capsys: pytest.CaptureFixture[str]
) -> None:
    prod = Settings(app_env=AppEnv.PROD, database_url=migrated_url)
    assert await run(["seed"], prod) == 1
    assert "SEED_NOT_ALLOWED" in capsys.readouterr().err
    async with await one_session(engine) as db:
        assert await vendors.list_all(db) == []
