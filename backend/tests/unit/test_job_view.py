import pytest

from app.services.job_view import mask_body, mask_number


@pytest.mark.parametrize(
    ("number", "masked"),
    [
        ("923001234567", "+9230*****567"),
        ("+92 300 1234567", "+9230*****567"),
        ("1234567", "+*******"),
        ("", "+"),
    ],
)
def test_numbers_keep_only_a_hint(number: str, masked: str) -> None:
    assert mask_number(number) == masked


def test_an_otp_shows_only_its_last_two_digits() -> None:
    assert mask_body("Aap ka OTP 482913 hai.", "otp") == "OTP ****13"
    assert mask_body("Code: 7781", "otp") == "OTP ****81"


def test_an_otp_without_a_code_shows_nothing_of_the_text() -> None:
    assert mask_body("hello there", "otp") == "otp (11 characters)"


@pytest.mark.parametrize("kind", ["transactional", "marketing"])
def test_other_messages_show_only_their_length(kind: str) -> None:
    masked = mask_body("Aap ka order 123456 ship ho gaya", kind)
    assert masked == f"{kind} (32 characters)"
    assert "123456" not in masked


def test_a_wiped_body_says_so() -> None:
    assert mask_body(None, "otp") == "(text removed)"
