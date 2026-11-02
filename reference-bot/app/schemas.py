"""Request and response shapes of the contract (ai-contract.md sections 3, 4 and 6).

The request models are lenient on purpose: unknown fields are ignored, so a newer gateway
that adds a field does not break this bot.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

MediaType = Literal["image", "sticker", "document", "ptt", "audio"]


class _Camel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore")


class Media(_Camel):
    has_media: bool = True
    media_type: MediaType
    mime_type: str = ""
    file_name: str = ""
    file_size: int = 0
    caption: str = ""
    base64_data: str | None = None  # None for files too big to send (section 4.7)
    skipped_large_file: bool = False
    skipped_combined_limit: bool = False


class IncomingMessage(_Camel):
    text: str = ""
    timestamp: str = ""
    media: Media | None = None


class HistoryMessage(_Camel):
    from_me: bool = False
    text: str = ""
    timestamp: str = ""


class BotRequest(_Camel):
    contract_version: int = 1
    request_id: str = ""
    chat_id: str = ""
    chat_title: str = ""
    sender_name: str = ""
    sender_number: str = ""
    is_saved: bool = False
    incoming_messages: list[IncomingMessage] = Field(default_factory=list)
    chat_history: list[HistoryMessage] = Field(default_factory=list)


class BotResponse(BaseModel):
    """``{"replies": [...]}``: each string is one WhatsApp bubble (max 5, 4000 chars each)."""

    replies: list[str]
