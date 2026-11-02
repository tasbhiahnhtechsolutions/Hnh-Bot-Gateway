"""Tests sign requests with their own copy of the contract formula (ai-contract.md section 5.1).

It is written out here on purpose instead of importing ``app.security``: if the bot's code and
the formula in the document ever drift apart, these tests must notice.
"""

import hashlib
import hmac
import json
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.main import create_app

SECRET = "whsec_test_secret_for_unit_tests"


def sign(secret: str, timestamp: int, raw: bytes) -> str:
    digest = hmac.new(secret.encode(), f"{timestamp}.".encode() + raw, hashlib.sha256).hexdigest()
    return f"v1={digest}"


def request_body(*messages: dict[str, Any], request_id: str = "req-1") -> dict[str, Any]:
    return {
        "contractVersion": 1,
        "requestId": request_id,
        "chatId": "923001234567@c.us",
        "chatTitle": "Sara Khan",
        "senderName": "Sara Khan",
        "senderNumber": "+923001234567",
        "isSaved": False,
        "incomingMessages": list(messages) or [text_message("hello")],
        "chatHistory": [{"fromMe": False, "text": "Hi", "timestamp": "19:10:05"}],
    }


def text_message(text: str) -> dict[str, Any]:
    return {"text": text, "timestamp": "19:30:12", "media": None}


def media_message(
    media_type: str,
    *,
    data: bytes | None = b"fake file bytes",
    mime: str = "application/octet-stream",
    name: str = "file",
    caption: str = "",
    size: int | None = None,
) -> dict[str, Any]:
    import base64

    return {
        "text": caption,
        "timestamp": "19:32:10",
        "media": {
            "hasMedia": True,
            "mediaType": media_type,
            "mimeType": mime,
            "fileName": name,
            "fileSize": len(data) if data is not None and size is None else (size or 0),
            "caption": caption,
            "base64Data": base64.b64encode(data).decode() if data is not None else None,
            **({"skippedLargeFile": True} if data is None else {}),
        },
    }


class Gateway:
    """Plays the gateway: signs a body and posts it to the bot."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client

    async def post(
        self,
        body: dict[str, Any] | bytes,
        *,
        secret: str = SECRET,
        timestamp: int | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        ts = int(time.time()) if timestamp is None else timestamp
        all_headers = {
            "Content-Type": "application/json",
            "X-Gateway-Timestamp": str(ts),
            "X-Gateway-Signature": sign(secret, ts, raw),
            "X-Contract-Version": "1",
            **(headers or {}),
        }
        return await self.client.post("/ai-reply", content=raw, headers=all_headers)


@pytest.fixture
async def gateway() -> AsyncIterator[Gateway]:
    app = create_app(Settings(bot_secret=SecretStr(SECRET)))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://bot"
    ) as client:
        yield Gateway(client)
