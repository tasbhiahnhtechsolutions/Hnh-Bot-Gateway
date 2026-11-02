"""Built-in fake vendor bot for ``mock_bot`` mode (plan section 7.1).

It follows the contract (it verifies the signature) and can misbehave on purpose, so we can
test our own failure handling. It runs in-process: no network, no SSRF path, no open port.
"""

import asyncio
import json
from enum import StrEnum

from fastapi import FastAPI, Request, Response

from app.core.signing import SignatureError, verify_signature


class MockScenario(StrEnum):
    OK = "ok"
    SLOW = "slow"
    ERROR500 = "error500"
    BADJSON = "badjson"
    EMPTY = "empty"
    HUGE = "huge"
    REDIRECT = "redirect"
    WRONGSHAPE = "wrongshape"


def _json(data: object, status: int = 200) -> Response:
    return Response(json.dumps(data), status_code=status, media_type="application/json")


def create_mock_bot_app(secret: str, scenario: MockScenario, *, slow_seconds: float) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.post("/ai-reply")
    async def ai_reply(request: Request) -> Response:
        raw = await request.body()
        try:
            verify_signature(secret, request.headers, raw)
        except SignatureError:
            return Response(status_code=401)

        if scenario is MockScenario.SLOW:
            await asyncio.sleep(slow_seconds)
        if scenario is MockScenario.ERROR500:
            return Response("mock bot exploded", status_code=500)
        if scenario is MockScenario.BADJSON:
            return Response("{not json", media_type="application/json")
        if scenario is MockScenario.EMPTY:
            return _json({"replies": []})
        if scenario is MockScenario.HUGE:
            return _json({"replies": ["x" * (4 * 1024 * 1024)]})
        if scenario is MockScenario.REDIRECT:
            return Response(
                status_code=302, headers={"Location": "http://169.254.169.254/latest/meta-data"}
            )
        if scenario is MockScenario.WRONGSHAPE:
            return _json({"replies": "this should be a list"})

        count = len(json.loads(raw).get("incomingMessages", []))
        return _json({"replies": [f"Mock bot: received {count} message(s)."]})

    return app
