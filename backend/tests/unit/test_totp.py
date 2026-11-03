import pytest

from app.core import totp

# RFC 6238 appendix B, SHA-1 secret "12345678901234567890".
# The RFC lists 8-digit codes; ours are 6 digits, so the last 6 of each.
RFC_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
RFC_VECTORS = [
    (59, "287082"),
    (1111111109, "081804"),
    (1111111111, "050471"),
    (1234567890, "005924"),
    (2000000000, "279037"),
]


@pytest.mark.parametrize(("seconds", "code"), RFC_VECTORS)
def test_codes_match_the_rfc_test_vectors(seconds: int, code: str) -> None:
    assert totp.code_for_step(RFC_SECRET, totp.current_step(seconds)) == code


@pytest.mark.parametrize(("seconds", "code"), RFC_VECTORS)
def test_verify_returns_the_step(seconds: int, code: str) -> None:
    assert totp.verify(RFC_SECRET, code, now=seconds) == totp.current_step(seconds)


def test_a_clock_one_step_off_is_tolerated_but_two_are_not() -> None:
    code = totp.code_for_step(RFC_SECRET, 100)
    assert totp.verify(RFC_SECRET, code, now=99 * 30) == 100
    assert totp.verify(RFC_SECRET, code, now=101 * 30) == 100
    assert totp.verify(RFC_SECRET, code, now=98 * 30) is None
    assert totp.verify(RFC_SECRET, code, now=102 * 30) is None


@pytest.mark.parametrize("bad", ["", "12345", "1234567", "abcdef", "12 34", "      ", "١٢٣٤٥٦"])
def test_garbage_codes_never_verify(bad: str) -> None:
    assert totp.verify(RFC_SECRET, bad, now=59) is None


def test_spaces_in_a_typed_code_are_ignored() -> None:
    assert totp.verify(RFC_SECRET, "287 082", now=59) is not None


def test_new_secrets_are_random_and_usable() -> None:
    first, second = totp.new_secret(), totp.new_secret()
    assert first != second
    assert len(first) == 32
    code = totp.code_for_step(first, 5)
    assert totp.verify(first, code, now=5 * 30) == 5


def test_the_otpauth_link_has_what_an_authenticator_needs() -> None:
    uri = totp.otpauth_uri("ABCDEF", "boss@example.com", "Vendor Gateway")
    assert uri.startswith("otpauth://totp/Vendor%20Gateway:boss%40example.com?")
    assert "secret=ABCDEF" in uri
    assert "digits=6" in uri
    assert "period=30" in uri
