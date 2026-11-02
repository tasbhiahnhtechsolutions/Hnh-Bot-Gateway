import json
import time
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.handlers import TOO_LARGE_REPLY, UNREADABLE_REPLY, build_replies
from app.main import create_app
from app.schemas import BotRequest
from tests.conftest import SECRET, Gateway, media_message, request_body, sign, text_message


async def replies_for(gateway: Gateway, *messages: dict[str, Any]) -> list[str]:
    response = await gateway.post(request_body(*messages))
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    assert list(body) == ["replies"]
    replies = body["replies"]
    assert isinstance(replies, list)
    assert all(isinstance(r, str) for r in replies)
    return replies


async def test_text_message_is_echoed(gateway: Gateway) -> None:
    replies = await replies_for(gateway, text_message("Red lehenga available hai?"))
    assert replies == ["Aap ne likha: Red lehenga available hai?"]


async def test_a_burst_of_messages_gets_one_answer(gateway: Gateway) -> None:
    replies = await replies_for(
        gateway, text_message("Hi"), text_message("I want a lehenga"), text_message("Red please")
    )
    assert len(replies) == 1
    assert "Hi | I want a lehenga | Red please" in replies[0]
    assert "3 messages" in replies[0]


@pytest.mark.parametrize(
    ("kind", "mime", "label"),
    [
        ("image", "image/jpeg", "tasveer"),
        ("sticker", "image/webp", "sticker"),
        ("document", "application/pdf", "document"),
        ("ptt", "audio/ogg", "voice note"),
        ("audio", "audio/mpeg", "audio"),
    ],
)
async def test_every_media_type_is_understood(
    gateway: Gateway, kind: str, mime: str, label: str
) -> None:
    replies = await replies_for(gateway, media_message(kind, mime=mime, data=b"x" * 2048))
    assert len(replies) == 1
    assert label in replies[0]
    assert mime in replies[0]
    assert "2.0 KB" in replies[0]  # proves the base64 was really decoded


async def test_image_with_caption_mentions_both(gateway: Gateway) -> None:
    replies = await replies_for(
        gateway, media_message("image", mime="image/jpeg", caption="Is this available?")
    )
    assert "Aap ne likha: Is this available?" in replies[0]
    assert "tasveer" in replies[0]


async def test_document_name_is_shown(gateway: Gateway) -> None:
    replies = await replies_for(
        gateway, media_message("document", mime="application/pdf", name="measurements.pdf")
    )
    assert "measurements.pdf" in replies[0]


async def test_large_file_with_null_base64_gets_a_polite_second_bubble(gateway: Gateway) -> None:
    large = media_message("document", data=None, size=31_457_280, name="catalog.pdf")
    assert large["media"]["base64Data"] is None
    replies = await replies_for(gateway, large)
    assert replies[-1] == TOO_LARGE_REPLY
    assert "30.0 MB" in replies[0]


async def test_corrupt_base64_does_not_crash(gateway: Gateway) -> None:
    message = media_message("image")
    message["media"]["base64Data"] = "***not base64***"
    replies = await replies_for(gateway, message)
    assert replies[-1] == UNREADABLE_REPLY


async def test_mixed_group_text_and_media(gateway: Gateway) -> None:
    replies = await replies_for(
        gateway,
        text_message("Dekhein"),
        media_message("ptt", mime="audio/ogg"),
        media_message("document", data=None, size=20_000_000),
    )
    assert replies[0].startswith("Aap ne likha: Dekhein")
    assert "voice note" in replies[0]
    assert replies[-1] == TOO_LARGE_REPLY


async def test_empty_text_and_no_media_still_gives_a_valid_answer(gateway: Gateway) -> None:
    body = await gateway.post(request_body(text_message("   ")))
    assert body.status_code == 200
    assert isinstance(body.json()["replies"], list)


async def test_no_messages_means_silence(gateway: Gateway) -> None:
    body = request_body()
    body["incomingMessages"] = []
    response = await gateway.post(body)
    assert response.status_code == 200
    assert response.json() == {"replies": []}


async def test_long_text_is_clipped_to_the_bubble_limit(gateway: Gateway) -> None:
    replies = await replies_for(gateway, text_message("a" * 20_000))
    assert len(replies) == 1
    assert len(replies[0]) <= 4000


async def test_big_body_with_a_real_sized_file_is_accepted(gateway: Gateway) -> None:
    replies = await replies_for(
        gateway, media_message("document", mime="application/pdf", data=b"\0" * (12 * 1024 * 1024))
    )
    assert "12.0 MB" in replies[0]


async def test_unknown_fields_are_ignored(gateway: Gateway) -> None:
    body = request_body(text_message("hi"))
    body["somethingNew"] = {"future": True}
    body["incomingMessages"][0]["extra"] = 1
    response = await gateway.post(body)
    assert response.status_code == 200


async def test_signed_but_malformed_bodies_get_400(gateway: Gateway) -> None:
    for raw in (b"{not json", b"[]", b'"text"', b'{"incomingMessages": "nope"}', b"null"):
        response = await gateway.post(raw)
        assert response.status_code == 400, raw


async def test_unknown_media_type_gets_400(gateway: Gateway) -> None:
    message = media_message("image")
    message["media"]["mediaType"] = "video"
    response = await gateway.post(request_body(message))
    assert response.status_code == 400


async def test_same_request_id_returns_the_same_saved_answer(gateway: Gateway) -> None:
    first = await gateway.post(request_body(text_message("first"), request_id="same-id"))
    again = await gateway.post(request_body(text_message("different text"), request_id="same-id"))
    other = await gateway.post(request_body(text_message("different text"), request_id="other"))
    assert again.json() == first.json()
    assert other.json() != first.json()


async def test_the_secret_never_appears_in_any_response(gateway: Gateway) -> None:
    for response in (
        await gateway.post(request_body()),
        await gateway.post(request_body(), secret="whsec_wrong"),
        await gateway.post(b"{not json"),
        await gateway.client.get("/health"),
    ):
        assert SECRET not in response.text


def test_build_replies_never_exceeds_the_contract_limits() -> None:
    request = BotRequest.model_validate(request_body(*[text_message("x" * 5000)] * 10))
    replies = build_replies(request)
    assert 0 < len(replies) <= 5
    assert all(len(r) <= 4000 for r in replies)


async def test_handler_does_not_log_message_text_or_files(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = create_app(Settings(bot_secret=SecretStr(SECRET)))
    raw = json.dumps(request_body(text_message("my private words"))).encode()
    ts = int(time.time())
    with caplog.at_level("DEBUG"):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://bot"
        ) as client:
            await client.post(
                "/ai-reply",
                content=raw,
                headers={
                    "X-Gateway-Timestamp": str(ts),
                    "X-Gateway-Signature": sign(SECRET, ts, raw),
                },
            )
    assert "my private words" not in caplog.text
    assert SECRET not in caplog.text
