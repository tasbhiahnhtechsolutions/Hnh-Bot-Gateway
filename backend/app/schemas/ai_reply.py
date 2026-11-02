"""Plugin -> gateway request and gateway -> bot request (ai-contract.md section 3 and 4)."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer
from pydantic.alias_generators import to_camel

CONTRACT_VERSION = 1
MAX_BUBBLES = 5
MAX_BUBBLE_CHARS = 4000

MediaType = Literal["image", "sticker", "document", "ptt", "audio"]


class _Camel(BaseModel):
    # Unknown fields (for example the old ``previewUrl`` / ``thumbnailBase64``) are dropped, so
    # they never reach the vendor bot (plan section 10).
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore")


class Media(_Camel):
    has_media: bool = True
    media_type: MediaType
    mime_type: str = ""
    file_name: str = ""
    file_size: int = Field(default=0, ge=0)
    caption: str = ""
    base64_data: str | None = None
    skipped_large_file: bool = False
    skipped_combined_limit: bool = False

    @model_serializer(mode="wrap")
    def _omit_false_skip_flags(self, handler: Any) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        for flag in ("skippedLargeFile", "skippedCombinedLimit"):
            if data.get(flag) is False:
                data.pop(flag, None)
        return data


class IncomingMessage(_Camel):
    text: str = ""
    timestamp: str = ""
    media: Media | None = None


class HistoryMessage(_Camel):
    from_me: bool = False
    text: str = ""
    timestamp: str = ""


class AiReplyRequest(_Camel):
    """What the plugin posts to ``/v1/ai-reply``."""

    chat_id: str = Field(min_length=1)
    chat_title: str = ""
    sender_name: str = ""
    sender_number: str = ""
    is_saved: bool = False
    incoming_messages: list[IncomingMessage] = Field(min_length=1)
    chat_history: list[HistoryMessage] = Field(default_factory=list)


def bot_request_body(payload: AiReplyRequest, request_id: str) -> dict[str, Any]:
    """The JSON object sent to the vendor bot: the contract fields in the documented order."""
    body: dict[str, Any] = {"contractVersion": CONTRACT_VERSION, "requestId": request_id}
    body.update(payload.model_dump(by_alias=True, mode="json"))
    return body


def redacted_summary(body: dict[str, Any]) -> dict[str, Any]:
    """Copy of a bot request body that is safe to show or log: file contents are replaced."""
    summary = dict(body)
    messages: list[dict[str, Any]] = []
    for message in body.get("incomingMessages", []):
        copy = dict(message)
        media = copy.get("media")
        if isinstance(media, dict) and media.get("base64Data") is not None:
            media = dict(media)
            media["base64Data"] = f"<redacted {len(media['base64Data'])} base64 chars>"
            copy["media"] = media
        messages.append(copy)
    summary["incomingMessages"] = messages
    return summary
