import uuid
from typing import Any

from app.models.canned_rule import CannedMatchType, CannedRule
from app.schemas.ai_reply import AiReplyRequest, IncomingMessage, Media
from app.services.canned import BUILTIN_DEFAULT_REPLY, pick_canned_reply
from app.services.echo import make_echo_replies


def rule(priority: int, kind: CannedMatchType, value: str, reply: str) -> CannedRule:
    return CannedRule(
        id=uuid.uuid4(),
        vendor_id=uuid.uuid4(),
        priority=priority,
        match_type=kind.value,
        match_value=value,
        reply_text=reply,
    )


RULES = [
    rule(1000, CannedMatchType.DEFAULT, "", "default"),
    rule(10, CannedMatchType.TEXT_CONTAINS, "price", "price reply"),
    rule(20, CannedMatchType.MEDIA_TYPE, "image", "image reply"),
    rule(30, CannedMatchType.MEDIA_TYPE, "ptt", "voice reply"),
]


def request(*messages: IncomingMessage) -> AiReplyRequest:
    return AiReplyRequest(chat_id="1@c.us", incoming_messages=list(messages))


def media(kind: Any, size: int = 0, caption: str = "") -> Media:
    return Media(media_type=kind, file_size=size, caption=caption)


def test_text_rule_is_case_insensitive_and_matches_anywhere() -> None:
    msg = IncomingMessage(text="Kya PRICE hai is ka?")
    assert pick_canned_reply(RULES, request(msg)) == ["price reply"]


def test_caption_counts_as_text() -> None:
    msg = IncomingMessage(text="", media=media("document", caption="price list"))
    assert pick_canned_reply(RULES, request(msg)) == ["price reply"]


def test_media_rules() -> None:
    assert pick_canned_reply(RULES, request(IncomingMessage(media=media("image")))) == [
        "image reply"
    ]
    assert pick_canned_reply(RULES, request(IncomingMessage(media=media("ptt")))) == ["voice reply"]


def test_priority_decides_when_several_rules_match() -> None:
    msg = IncomingMessage(text="price?", media=media("image"))
    assert pick_canned_reply(RULES, request(msg)) == ["price reply"]


def test_any_message_of_the_burst_can_match() -> None:
    first, second = IncomingMessage(text="hi"), IncomingMessage(text="ptt?", media=media("ptt"))
    assert pick_canned_reply(RULES, request(first, second)) == ["voice reply"]


def test_default_rule_then_builtin() -> None:
    assert pick_canned_reply(RULES, request(IncomingMessage(text="hello"))) == ["default"]
    assert pick_canned_reply([], request(IncomingMessage(text="hello"))) == [BUILTIN_DEFAULT_REPLY]


def test_empty_match_value_never_matches_everything() -> None:
    rules = [rule(1, CannedMatchType.TEXT_CONTAINS, "", "oops")]
    assert pick_canned_reply(rules, request(IncomingMessage(text="x"))) == [BUILTIN_DEFAULT_REPLY]


def test_echo_text_only() -> None:
    assert make_echo_replies(request(IncomingMessage(text="Red lehenga available hai?"))) == [
        "Aap ne likha: Red lehenga available hai?"
    ]


def test_echo_joins_burst_and_summarises_media() -> None:
    reply = make_echo_replies(
        request(
            IncomingMessage(text="Is available?", media=media("image", size=14_000)),
            IncomingMessage(text="hello"),
        )
    )
    assert reply == ["Aap ne likha: Is available? / hello (1 image, 14 KB)"]


def test_echo_without_text_and_with_skipped_file() -> None:
    reply = make_echo_replies(request(IncomingMessage(media=media("document", size=0))))
    assert reply == ["Aap ne likha: (no text) (1 document)"]
