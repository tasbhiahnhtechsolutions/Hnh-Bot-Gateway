"""The bot's brain: a deliberately simple echo with a media summary. No AI here.

Replace ``build_replies`` with your own logic. Everything else (signature check, parsing,
limits) is already done for you in ``main.py``.
"""

import base64
import binascii

from app.schemas import BotRequest, IncomingMessage, Media

MAX_BUBBLES = 5
MAX_BUBBLE_CHARS = 4000
ECHO_LIMIT = 1500

TOO_LARGE_REPLY = "Aap ki file bohat bari hai. Barae meherbani chhoti file bhejein."
UNREADABLE_REPLY = "Aap ki file khul nahi saki. Barae meherbani dobara bhejein."

_KIND_LABELS = {
    "image": "tasveer",
    "sticker": "sticker",
    "document": "document",
    "ptt": "voice note",
    "audio": "audio",
}


def _kb(size: int) -> str:
    return f"{size / 1024:.1f} KB" if size < 1024 * 1024 else f"{size / 1024 / 1024:.1f} MB"


def _decoded_size(media: Media) -> int | None:
    """Size of the real file, or None when ``base64Data`` is not valid base64."""
    assert media.base64_data is not None
    try:
        return len(base64.b64decode(media.base64_data, validate=True))
    except (binascii.Error, ValueError):
        return None


def _describe(media: Media) -> tuple[str, str | None]:
    """``(short description, problem)`` where problem is 'large', 'unreadable' or None."""
    label = _KIND_LABELS[media.media_type]
    if media.base64_data is None:
        return f"{label} ({_kb(media.file_size)}, bohat bari)", "large"
    size = _decoded_size(media)
    if size is None:
        return f"{label} (kharab data)", "unreadable"
    name = f" {media.file_name}" if media.media_type == "document" and media.file_name else ""
    return f"{label}{name} ({media.mime_type or 'unknown type'}, {_kb(size)})", None


def _clip(text: str, limit: int = ECHO_LIMIT) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def build_replies(request: BotRequest) -> list[str]:
    """One answer for the whole group of ``incomingMessages`` (contract section 3.4)."""
    messages: list[IncomingMessage] = request.incoming_messages
    if not messages:
        return []

    texts = [m.text.strip() for m in messages if m.text.strip()]
    descriptions: list[str] = []
    problems: set[str] = set()
    for message in messages:
        if message.media is not None:
            description, problem = _describe(message.media)
            descriptions.append(description)
            if problem:
                problems.add(problem)

    parts: list[str] = []
    if texts:
        parts.append("Aap ne likha: " + " | ".join(texts))
    if descriptions:
        verb = "bheji" if not texts else "aur bheja"
        parts.append(f"Aap ne yeh {verb}: " + ", ".join(descriptions) + ".")
    if len(messages) > 1:
        parts.append(f"({len(messages)} messages ek saath mile.)")

    replies = [_clip(" ".join(parts))]
    if "large" in problems:
        replies.append(TOO_LARGE_REPLY)
    if "unreadable" in problems:
        replies.append(UNREADABLE_REPLY)
    return [r[:MAX_BUBBLE_CHARS] for r in replies[:MAX_BUBBLES]]
