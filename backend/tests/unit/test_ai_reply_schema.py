import json
from typing import Any

import pytest
from pydantic import ValidationError

from app.schemas.ai_reply import AiReplyRequest, bot_request_body, redacted_summary

BASE: dict[str, Any] = {
    "chatId": "923001234567@c.us",
    "chatTitle": "Sara Khan",
    "senderName": "Sara Khan",
    "senderNumber": "+923001234567",
    "isSaved": False,
    "incomingMessages": [{"text": "hi", "timestamp": "19:30:12", "media": None}],
    "chatHistory": [{"fromMe": True, "text": "Hello", "timestamp": "19:10:20"}],
}


def load(**overrides: Any) -> AiReplyRequest:
    return AiReplyRequest.model_validate_json(json.dumps({**BASE, **overrides}))


def media(**fields: Any) -> dict[str, Any]:
    return {
        "hasMedia": True,
        "mediaType": "image",
        "mimeType": "image/jpeg",
        "fileName": "image_1",
        "fileSize": 10,
        "caption": "c",
        "base64Data": "AAAA",
        **fields,
    }


def test_body_for_the_bot_starts_with_version_and_request_id_and_keeps_camel_case() -> None:
    body = bot_request_body(load(), "req-1")
    assert list(body)[:3] == ["contractVersion", "requestId", "chatId"]
    assert body["contractVersion"] == 1
    assert body["requestId"] == "req-1"
    assert body["chatHistory"] == [{"fromMe": True, "text": "Hello", "timestamp": "19:10:20"}]
    assert body["incomingMessages"] == [{"text": "hi", "timestamp": "19:30:12", "media": None}]


def test_unknown_plugin_fields_never_reach_the_bot() -> None:
    payload = load(
        incomingMessages=[
            {
                "text": "x",
                "timestamp": "1",
                "media": media(previewUrl="blob:x", thumbnailBase64="T"),
            }
        ],
        somethingElse="leak",
    )
    text = json.dumps(bot_request_body(payload, "r"))
    assert "previewUrl" not in text
    assert "thumbnailBase64" not in text
    assert "somethingElse" not in text


def test_skip_flags_only_appear_when_true() -> None:
    plain = bot_request_body(
        load(incomingMessages=[{"text": "", "timestamp": "1", "media": media()}]), "r"
    )
    assert "skippedLargeFile" not in plain["incomingMessages"][0]["media"]

    big = media(base64Data=None, skippedLargeFile=True)
    body = bot_request_body(
        load(incomingMessages=[{"text": "", "timestamp": "1", "media": big}]), "r"
    )
    sent = body["incomingMessages"][0]["media"]
    assert sent["skippedLargeFile"] is True
    assert sent["base64Data"] is None
    assert "skippedCombinedLimit" not in sent


@pytest.mark.parametrize("kind", ["image", "sticker", "document", "ptt", "audio"])
def test_all_five_media_types_are_accepted(kind: str) -> None:
    load(incomingMessages=[{"text": "", "timestamp": "1", "media": media(mediaType=kind)}])


@pytest.mark.parametrize(
    "bad",
    [
        {"incomingMessages": []},
        {"incomingMessages": "hi"},
        {"incomingMessages": [{"text": "", "media": media(mediaType="video")}]},
        {"incomingMessages": [{"text": 5}]},
        {"chatId": ""},
        {"chatId": None},
        {"isSaved": "maybe"},
    ],
)
def test_invalid_requests_are_rejected(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        load(**bad)


def test_optional_fields_default_so_a_minimal_request_works() -> None:
    payload = AiReplyRequest.model_validate_json(
        '{"chatId": "1@c.us", "incomingMessages": [{"text": "hi"}]}'
    )
    body = bot_request_body(payload, "r")
    assert body["senderName"] == ""
    assert body["chatHistory"] == []
    assert body["incomingMessages"][0]["media"] is None


def test_redacted_summary_hides_file_contents_and_keeps_the_original_intact() -> None:
    body = bot_request_body(
        load(
            incomingMessages=[{"text": "t", "timestamp": "1", "media": media(base64Data="A" * 100)}]
        ),
        "r",
    )
    summary = redacted_summary(body)
    assert "AAAA" not in json.dumps(summary)
    assert "100 base64 chars" in json.dumps(summary)
    assert body["incomingMessages"][0]["media"]["base64Data"] == "A" * 100
