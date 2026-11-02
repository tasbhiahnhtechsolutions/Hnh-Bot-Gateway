import uuid

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import AppEnv, Settings
from app.core.errors import ApiError
from app.core.security import SecretBox
from app.models import InboundMode, Vendor
from app.schemas.ai_reply import AiReplyRequest, IncomingMessage
from app.services.inbound import InboundDeps, process_inbound
from app.services.mock_bot import MockScenario, create_mock_bot_app

BOX = SecretBox(Fernet.generate_key())


def deps(**overrides: object) -> InboundDeps:
    values: dict[str, object] = {
        "app_env": AppEnv.TEST,
        "secret_encryption_key": SecretStr(Fernet.generate_key().decode()),
        "enable_mock_bot": True,
        "bot_timeout_seconds": 5.0,
        "mock_bot_slow_seconds": 3.0,
    }
    values.update(overrides)
    return InboundDeps(settings=Settings(_env_file=None, **values), box=BOX)  # type: ignore[arg-type]


def vendor() -> Vendor:
    return Vendor(
        id=uuid.uuid4(),
        inbound_mode=InboundMode.MOCK_BOT.value,
        bot_secret_enc=BOX.encrypt("whsec_mock"),
    )


PAYLOAD = AiReplyRequest(
    chat_id="1@c.us", incoming_messages=[IncomingMessage(text="a"), IncomingMessage(text="b")]
)
NO_DB: AsyncSession = None  # type: ignore[assignment]  # mock_bot mode never touches the database


async def run(scenario: MockScenario, d: InboundDeps | None = None) -> list[str]:
    result = await process_inbound(NO_DB, d or deps(), vendor(), PAYLOAD, "r", scenario=scenario)
    return result.replies


async def test_ok_scenario_answers_through_the_real_signed_path() -> None:
    assert await run(MockScenario.OK) == ["Mock bot: received 2 message(s)."]


async def test_empty_scenario_is_silence() -> None:
    assert await run(MockScenario.EMPTY) == []


@pytest.mark.parametrize(
    ("scenario", "status", "code"),
    [
        (MockScenario.ERROR500, 502, "BOT_HTTP_ERROR"),
        (MockScenario.BADJSON, 502, "BOT_INVALID_RESPONSE"),
        (MockScenario.WRONGSHAPE, 502, "BOT_INVALID_RESPONSE"),
        (MockScenario.REDIRECT, 502, "BOT_REDIRECT_NOT_ALLOWED"),
    ],
)
async def test_failure_scenarios_map_to_the_documented_errors(
    scenario: MockScenario, status: int, code: str
) -> None:
    with pytest.raises(ApiError) as info:
        await run(scenario)
    assert (info.value.status_code, info.value.code) == (status, code)


async def test_huge_scenario_hits_the_response_size_limit() -> None:
    with pytest.raises(ApiError) as info:
        await run(MockScenario.HUGE)
    assert info.value.code == "BOT_RESPONSE_TOO_LARGE"


async def test_slow_scenario_times_out() -> None:
    with pytest.raises(ApiError) as info:
        await run(MockScenario.SLOW, deps(bot_timeout_seconds=0.3))
    assert (info.value.status_code, info.value.code) == (504, "BOT_TIMEOUT")


async def test_mock_bot_is_refused_when_disabled() -> None:
    with pytest.raises(ApiError) as info:
        await run(MockScenario.OK, deps(enable_mock_bot=False))
    assert (info.value.status_code, info.value.code) == (503, "MOCK_BOT_DISABLED")


async def test_mock_bot_rejects_a_request_signed_with_another_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The mock really verifies signatures: a wrong secret must give HTTP 401 from the bot."""
    real = create_mock_bot_app

    def with_other_secret(_secret: str, scenario: MockScenario, *, slow_seconds: float) -> FastAPI:
        return real("whsec_different", scenario, slow_seconds=slow_seconds)

    monkeypatch.setattr("app.services.inbound.create_mock_bot_app", with_other_secret)
    with pytest.raises(ApiError) as info:
        await process_inbound(NO_DB, deps(), vendor(), PAYLOAD, "r")
    assert info.value.code == "BOT_HTTP_ERROR"
    assert "401" in info.value.message
