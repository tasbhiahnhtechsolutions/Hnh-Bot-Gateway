"""The same pipeline over a real TCP connection to a real HTTP server (no fake transport)."""

import asyncio
import socket
from collections.abc import AsyncIterator

import pytest
import uvicorn
from fastapi import FastAPI, Request, Response

from app.core.signing import SignatureError, verify_signature
from tests.integration.conftest import MakeApi


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def build_bot(secret_holder: dict[str, str], seen: list[str]) -> FastAPI:
    app = FastAPI()

    @app.post("/ai-reply")
    async def ai_reply(request: Request) -> Response:
        raw = await request.body()
        try:
            verify_signature(secret_holder["secret"], request.headers, raw)
        except SignatureError:
            return Response(status_code=401)
        seen.append(raw.decode())
        return Response('{"replies": ["real bot says hi"]}', media_type="application/json")

    @app.post("/redirect/ai-reply")
    async def redirect() -> Response:
        return Response(status_code=302, headers={"Location": "http://127.0.0.1:1/never"})

    @app.post("/slow/ai-reply")
    async def slow() -> Response:
        await asyncio.sleep(5)
        return Response('{"replies": []}', media_type="application/json")

    @app.post("/big/ai-reply")
    async def big() -> Response:
        return Response('{"replies": ["' + "x" * 3_000_000 + '"]}', media_type="application/json")

    return app


@pytest.fixture
async def live_bot() -> AsyncIterator[tuple[str, dict[str, str], list[str]]]:
    secret: dict[str, str] = {}
    seen: list[str] = []
    port = free_port()
    config = uvicorn.Config(build_bot(secret, seen), host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    while not server.started:  # noqa: ASYNC110  (uvicorn offers no start event)
        await asyncio.sleep(0.02)
    yield f"http://127.0.0.1:{port}", secret, seen
    server.should_exit = True
    await task


async def test_real_server_accepts_our_signature_including_unicode(
    make_api: MakeApi, live_bot: tuple[str, dict[str, str], list[str]]
) -> None:
    base, secret, seen = live_bot
    api = await make_api()
    api.app.state.inbound_deps = type(api.app.state.inbound_deps)(
        settings=api.settings, box=api.box
    )
    created = await api.vendor(bot_base_url=base + "/ai-reply")
    secret["secret"] = created.secrets.bot_secret

    body = {"chatId": "1@c.us", "incomingMessages": [{"text": "سلام 👋 \n new line"}]}
    response = await api.post_reply(created.secrets.live_key, body)

    assert response.status_code == 200, response.text
    assert response.json() == {"replies": ["real bot says hi"]}
    assert "سلام 👋" in seen[0]


async def test_real_server_with_another_secret_answers_401_which_becomes_502(
    make_api: MakeApi, live_bot: tuple[str, dict[str, str], list[str]]
) -> None:
    base, secret, _ = live_bot
    api = await make_api()
    api.app.state.inbound_deps = type(api.app.state.inbound_deps)(
        settings=api.settings, box=api.box
    )
    created = await api.vendor(bot_base_url=base + "/ai-reply")
    secret["secret"] = "whsec_not_the_vendors_secret"

    response = await api.post_reply(created.secrets.live_key)
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "BOT_HTTP_ERROR"


@pytest.mark.parametrize(
    ("path", "status", "code", "overrides"),
    [
        ("/redirect", 502, "BOT_REDIRECT_NOT_ALLOWED", {}),
        ("/slow", 504, "BOT_TIMEOUT", {"bot_timeout_seconds": 0.5}),
        ("/big", 502, "BOT_RESPONSE_TOO_LARGE", {}),
    ],
)
async def test_real_server_misbehaviour(
    make_api: MakeApi,
    live_bot: tuple[str, dict[str, str], list[str]],
    path: str,
    status: int,
    code: str,
    overrides: dict[str, object],
) -> None:
    base, secret, _ = live_bot
    api = await make_api(**overrides)
    api.app.state.inbound_deps = type(api.app.state.inbound_deps)(
        settings=api.settings, box=api.box
    )
    created = await api.vendor(bot_base_url=base + path + "/ai-reply")
    secret["secret"] = created.secrets.bot_secret

    response = await api.post_reply(created.secrets.live_key)
    assert (response.status_code, response.json()["error"]["code"]) == (status, code)
