"""Check what a vendor bot answered (ai-contract.md section 6)."""

import json
from typing import Any

from app.schemas.ai_reply import MAX_BUBBLE_CHARS, MAX_BUBBLES


def parse_bot_response(raw: bytes) -> tuple[list[str] | None, list[str]]:
    """Return ``(replies, errors)``. ``replies`` is None when the answer breaks the contract.

    Extra bubbles (more than 5) are dropped, long bubbles are cut to 4000 characters and blank
    bubbles are skipped, as the contract allows. Wrong shapes are errors, never guessed at.
    """
    try:
        data: Any = json.loads(raw)
    except (ValueError, RecursionError):
        return None, ["response body is not valid JSON"]

    if not isinstance(data, dict):
        return None, [f"response must be a JSON object, got {_kind(data)}"]
    if "replies" not in data:
        hint = " (found 'reply'; the key must be named 'replies')" if "reply" in data else ""
        return None, [f"response has no 'replies' key{hint}"]

    replies = data["replies"]
    if not isinstance(replies, list):
        return None, [f"'replies' must be a list of strings, got {_kind(replies)}"]

    errors = [
        f"replies[{i}] must be a string, got {_kind(item)}"
        for i, item in enumerate(replies)
        if not isinstance(item, str)
    ]
    if errors:
        return None, errors

    bubbles = [item[:MAX_BUBBLE_CHARS] for item in replies if item.strip()]
    return bubbles[:MAX_BUBBLES], []


def _kind(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int | float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__
