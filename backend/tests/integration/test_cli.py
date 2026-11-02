import base64
import json
import re
import uuid
from pathlib import Path
from typing import Any

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
        enable_mock_bot=True,
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


# --- simulate-incoming -----------------------------------------------------------------------


async def vendor_id_of(engine: AsyncEngine, name: str) -> str:
    async with await one_session(engine) as db:
        vendor = await vendors.get_by_business_name(db, name)
        assert vendor is not None
        return str(vendor.id)


async def simulate(
    settings: Settings, capsys: pytest.CaptureFixture[str], *argv: str
) -> tuple[int, dict[str, Any]]:
    code = await run(["simulate-incoming", *argv], settings)
    out = capsys.readouterr().out
    return code, json.loads(out) if out.strip().startswith("{") else {}


async def test_simulate_canned_uses_the_seeded_rules_without_calling_a_bot(
    settings: Settings, engine: AsyncEngine, capsys: pytest.CaptureFixture[str]
) -> None:
    await run(["seed"], settings)
    capsys.readouterr()
    vid = await vendor_id_of(engine, "demo-canned")

    code, report = await simulate(settings, capsys, vid, "--text", "kya price hai?")
    assert code == 0
    assert report["ok"] is True
    assert report["mode"] == "canned"
    assert report["replies"] == ["Price 85,000 se shuru hota hai."]
    assert report["bot"] is None

    code, report = await simulate(settings, capsys, vid, "--text", "hello")
    assert report["replies"] == ["Shukriya! Hum jald jawab denge."]


async def test_simulate_with_an_attached_image(
    settings: Settings, engine: AsyncEngine, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    await run(["seed"], settings)
    capsys.readouterr()
    picture = tmp_path / "dress.jpg"
    picture.write_bytes(b"\xff\xd8\xff fake jpeg bytes")

    code, report = await simulate(
        settings, capsys, await vendor_id_of(engine, "demo-canned"), "--file", str(picture)
    )
    assert code == 0
    assert report["replies"] == ["Photo mil gayi, jaldi dekhte hain."]

    code, report = await simulate(
        settings,
        capsys,
        await vendor_id_of(engine, "demo-echo"),
        "--text",
        "look",
        "--file",
        str(picture),
    )
    assert report["replies"] == ["Aap ne likha: look (1 image, 1 KB)"]


async def test_simulate_mock_bot_report_shows_headers_and_hides_file_contents(
    settings: Settings, engine: AsyncEngine, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    await run(["seed"], settings)
    capsys.readouterr()
    vid = await vendor_id_of(engine, "demo-mockbot")
    doc = tmp_path / "measurements.pdf"
    doc.write_bytes(b"%PDF-1.4 SECRET-FILE-CONTENT")

    code, report = await simulate(settings, capsys, vid, "--text", "hi", "--file", str(doc))
    assert code == 0
    assert report["replies"] == ["Mock bot: received 1 message(s)."]
    bot = report["bot"]
    assert bot["responseStatus"] == 200
    assert bot["requestHeaders"]["X-Gateway-Signature"].startswith("v1=")
    assert bot["requestHeaders"]["X-Gateway-Timestamp"].isdigit()
    assert bot["validationErrors"] == []
    assert "SECRET-FILE-CONTENT" not in json.dumps(report)
    assert base64.b64encode(b"%PDF-1.4 SECRET-FILE-CONTENT").decode() not in json.dumps(report)
    media = bot["request"]["incomingMessages"][0]["media"]
    assert media["mediaType"] == "document"
    assert media["base64Data"].startswith("<redacted")


@pytest.mark.parametrize(
    ("scenario", "code"),
    [
        ("error500", "BOT_HTTP_ERROR"),
        ("badjson", "BOT_INVALID_RESPONSE"),
        ("wrongshape", "BOT_INVALID_RESPONSE"),
        ("redirect", "BOT_REDIRECT_NOT_ALLOWED"),
        ("huge", "BOT_RESPONSE_TOO_LARGE"),
    ],
)
async def test_simulate_failure_scenarios_print_a_report_and_exit_3(
    settings: Settings,
    engine: AsyncEngine,
    capsys: pytest.CaptureFixture[str],
    scenario: str,
    code: str,
) -> None:
    await run(["seed"], settings)
    capsys.readouterr()
    vid = await vendor_id_of(engine, "demo-mockbot")

    exit_code, report = await simulate(
        settings, capsys, vid, "--text", "hi", "--scenario", scenario
    )
    assert exit_code == 3
    assert report["ok"] is False
    assert report["errorCode"] == code
    assert report["replies"] == []
    if code == "BOT_INVALID_RESPONSE":
        assert report["bot"]["validationErrors"]


async def test_simulate_empty_scenario_is_silence_not_an_error(
    settings: Settings, engine: AsyncEngine, capsys: pytest.CaptureFixture[str]
) -> None:
    await run(["seed"], settings)
    capsys.readouterr()
    vid = await vendor_id_of(engine, "demo-mockbot")
    exit_code, report = await simulate(settings, capsys, vid, "--text", "hi", "--scenario", "empty")
    assert (exit_code, report["ok"], report["replies"]) == (0, True, [])


async def test_simulate_input_errors(
    settings: Settings, engine: AsyncEngine, capsys: pytest.CaptureFixture[str]
) -> None:
    await run(["seed"], settings)
    capsys.readouterr()
    vid = await vendor_id_of(engine, "demo-echo")

    assert await run(["simulate-incoming", vid], settings) == 1
    assert "give --text and/or --file" in capsys.readouterr().err
    assert await run(["simulate-incoming", str(uuid.uuid4()), "--text", "x"], settings) == 1
    assert "VENDOR_NOT_FOUND" in capsys.readouterr().err


async def test_test_bot_reports_fail_when_nothing_listens_and_exits_3(
    settings: Settings, engine: AsyncEngine, capsys: pytest.CaptureFixture[str]
) -> None:
    await run(["seed"], settings)
    capsys.readouterr()
    vid = await vendor_id_of(engine, "demo-canned")  # canned mode, bot URL localhost:9000
    assert await run(["test-bot", vid], settings) == 3
    out = capsys.readouterr().out
    assert "FAIL: BOT_UNREACHABLE" in out
    assert json.loads(out.split("\nFAIL")[0])["mode"] == "forward"


async def test_test_bot_unknown_vendor(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await run(["test-bot", str(uuid.uuid4())], settings) == 1
    assert "VENDOR_NOT_FOUND" in capsys.readouterr().err
