"""Customer phone number normalisation (plan section 8.1, tests in section 14.1)."""

import re

from app.core.errors import ApiError

_NOISE = re.compile(r"[\s\-().]")


def normalize_customer_number(raw: str, default_country_code: str = "92") -> str:
    """Return digits with country code, e.g. ``03486102017`` -> ``923486102017``.

    Accepts ``0348...``, ``+92 348 ...``, ``0092 348 ...``, ``92-348-...`` and a bare national
    number. Letters or anything else that is not a phone number raise ``INVALID_NUMBER``.
    """
    text = _NOISE.sub("", raw.strip())
    plus = text.startswith("+")
    digits = text.removeprefix("+")
    if not digits or not digits.isascii() or not digits.isdigit():
        raise ApiError(400, "INVALID_NUMBER", "number must contain digits only")

    if plus:
        pass  # already international
    elif digits.startswith("00"):
        digits = digits[2:]
    elif digits.startswith("0"):
        digits = default_country_code + digits.lstrip("0")
    elif not digits.startswith(default_country_code) and len(digits) <= 10:
        digits = default_country_code + digits  # national number written without the zero

    if not 10 <= len(digits) <= 15 or digits.startswith("0"):
        raise ApiError(400, "INVALID_NUMBER", "number must be 10 to 15 digits")
    return digits
