"""``canned`` inbound mode: pick a hardcoded reply. No call to any bot (plan section 7.1)."""

from collections.abc import Sequence

from app.models.canned_rule import CannedMatchType, CannedRule
from app.schemas.ai_reply import AiReplyRequest

BUILTIN_DEFAULT_REPLY = "Shukriya! Hum jald jawab denge."


def pick_canned_reply(rules: Sequence[CannedRule], payload: AiReplyRequest) -> list[str]:
    """First matching rule by priority wins; otherwise the default rule; otherwise a built-in."""
    texts = [
        part.lower()
        for m in payload.incoming_messages
        for part in (m.text, m.media.caption if m.media else "")
        if part
    ]
    media_types = {m.media.media_type for m in payload.incoming_messages if m.media is not None}

    default: str | None = None
    for rule in sorted(rules, key=lambda r: r.priority):
        if rule.match_type == CannedMatchType.TEXT_CONTAINS:
            needle = rule.match_value.lower()
            if needle and any(needle in text for text in texts):
                return [rule.reply_text]
        elif rule.match_type == CannedMatchType.MEDIA_TYPE:
            if rule.match_value in media_types:
                return [rule.reply_text]
        elif rule.match_type == CannedMatchType.DEFAULT and default is None:
            default = rule.reply_text
    return [default if default is not None else BUILTIN_DEFAULT_REPLY]
