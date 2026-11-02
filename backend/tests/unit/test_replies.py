import json
from typing import Any

import pytest

from app.services.replies import parse_bot_response


def parse(data: Any) -> tuple[list[str] | None, list[str]]:
    return parse_bot_response(json.dumps(data).encode())


def test_good_answers() -> None:
    assert parse({"replies": ["Ji bataiye"]}) == (["Ji bataiye"], [])
    assert parse({"replies": []}) == ([], [])
    assert parse({"replies": ["a"], "debug": {"x": 1}}) == (["a"], [])


@pytest.mark.parametrize(
    ("data", "fragment"),
    [
        ({"replies": "Ji bataiye"}, "got string"),
        ({"replies": None}, "got null"),
        ({"replies": {"a": 1}}, "got object"),
        ({"replies": 5}, "got number"),
        ({"replies": [1, 2]}, "replies[0] must be a string"),
        ({"replies": ["ok", None]}, "replies[1] must be a string, got null"),
        ({"replies": [["nested"]]}, "got list"),
        ({"reply": ["Ji bataiye"]}, "found 'reply'"),
        ({}, "no 'replies' key"),
        (["Ji bataiye"], "got list"),
        ("Ji bataiye", "got string"),
        (None, "got null"),
        (12, "got number"),
    ],
)
def test_contract_violations(data: Any, fragment: str) -> None:
    replies, errors = parse(data)
    assert replies is None
    assert fragment in errors[0]


@pytest.mark.parametrize("raw", [b"", b"{not json", b"\xff\xfe", b"[", b"nul"])
def test_not_json(raw: bytes) -> None:
    replies, errors = parse_bot_response(raw)
    assert replies is None
    assert errors == ["response body is not valid JSON"]


def test_more_than_five_bubbles_are_cut() -> None:
    replies, _ = parse({"replies": [f"m{i}" for i in range(6)]})
    assert replies == ["m0", "m1", "m2", "m3", "m4"]


def test_long_bubble_is_cut_to_4000_and_exact_4000_is_kept() -> None:
    replies, _ = parse({"replies": ["a" * 5000, "b" * 4000]})
    assert replies is not None
    assert [len(r) for r in replies] == [4000, 4000]


def test_blank_bubbles_are_skipped_before_counting() -> None:
    replies, _ = parse({"replies": ["", "  ", "a", "\n", "b", "c", "d", "e", "f"]})
    assert replies == ["a", "b", "c", "d", "e"]


def test_urdu_and_emoji_survive() -> None:
    assert parse({"replies": ["جی بتائیے 👍"]}) == (["جی بتائیے 👍"], [])
