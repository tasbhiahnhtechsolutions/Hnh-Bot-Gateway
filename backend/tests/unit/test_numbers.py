import pytest

from app.core.errors import ApiError
from app.core.numbers import normalize_customer_number


@pytest.mark.parametrize(
    "raw",
    [
        "03486102017",
        "+92 348 6102017",
        "0092 348 6102017",
        "3486102017",
        "92-348-6102017",
        "(92) 348 6102017",
        "  923486102017  ",
        "+923486102017",
        "0348-6102017",
    ],
)
def test_pakistani_numbers_in_every_common_format(raw: str) -> None:
    assert normalize_customer_number(raw) == "923486102017"


def test_other_country_with_plus_is_kept() -> None:
    assert normalize_customer_number("+1 415 555 2671") == "14155552671"
    assert normalize_customer_number("0044 7911 123456") == "447911123456"


def test_default_country_code_is_used_for_local_numbers() -> None:
    assert normalize_customer_number("07911123456", "44") == "447911123456"


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "abc",
        "0348abc6102",
        "03486102017x",
        "+",
        "12345",
        "1" * 16,
        "+00123456789012",
        "٠٣٤٨٦١٠٢٠١٧",
    ],
)
def test_bad_numbers_are_refused(raw: str) -> None:
    with pytest.raises(ApiError) as info:
        normalize_customer_number(raw)
    assert info.value.status_code == 400
    assert info.value.code == "INVALID_NUMBER"
