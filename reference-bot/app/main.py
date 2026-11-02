"""Reference vendor bot: ``POST /ai-reply`` (ai-contract.md) and ``GET /health``.

Run: ``BOT_SECRET=whsec_... uv run python -m app`` (port 9000).
"""

import json
from collections import OrderedDict

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.config import Settings, get_settings
from app.handlers import build_replies
from app.schemas import BotRequest, BotResponse
from app.security import SignatureError, verify_signature

REMEMBERED_REQUESTS = 1000


def _bad_request(message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=400)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    secret = settings.bot_secret.get_secret_value()
    # The gateway sends the SAME requestId when it retries, so we answer the same way (section 7).
    answers: OrderedDict[str, BotResponse] = OrderedDict()

    app = FastAPI(title="Reference bot", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/ai-reply")
    async def ai_reply(request: Request) -> Response:
        raw = await request.body()  # raw bytes first: the signature covers exactly these
        try:
            verify_signature(secret, request.headers, raw, tolerance=settings.max_skew_seconds)
        except SignatureError:
            return Response(status_code=401)  # no detail: do not help an attacker

        try:
            payload = BotRequest.model_validate(json.loads(raw))
        except (ValueError, ValidationError):
            return _bad_request("body is not a valid ai-reply request")

        if payload.request_id and payload.request_id in answers:
            return JSONResponse(answers[payload.request_id].model_dump())

        reply = BotResponse(replies=build_replies(payload))
        if payload.request_id:
            answers[payload.request_id] = reply
            while len(answers) > REMEMBERED_REQUESTS:
                answers.popitem(last=False)
        return JSONResponse(reply.model_dump())

    return app
