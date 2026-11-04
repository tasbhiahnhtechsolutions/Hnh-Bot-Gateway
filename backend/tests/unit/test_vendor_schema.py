import pytest
from pydantic import ValidationError

from app.models import InboundMode
from app.schemas.vendor import VendorCreate


def make(**overrides: object) -> VendorCreate:
    values: dict[str, object] = {
        "business_name": "Ali Boutique",
        "whatsapp_number": "923001234567",
        "email": "ali@example.com",
        "bot_base_url": "https://bot.example.com",
    }
    values.update(overrides)
    return VendorCreate(**values)


def test_defaults_match_the_plan() -> None:
    vendor = make()
    assert vendor.default_country_code == "92"
    assert vendor.marketing_daily_cap == 200
    assert vendor.inbound_mode is InboundMode.FORWARD
    assert vendor.status_webhook_url is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("923001234567", "923001234567"),
        ("+92 300 1234567", "923001234567"),
        ("+92-300-123-4567", "923001234567"),
        ("(92) 3001234567", "923001234567"),
    ],
)
def test_number_is_normalized(raw: str, expected: str) -> None:
    assert make(whatsapp_number=raw).whatsapp_number == expected


@pytest.mark.parametrize(
    "raw",
    ["", "abc", "92300123456a", "123456789", "1234567890123456", "٩٢٣٠٠١٢٣٤٥٦٧", "92+300123456"],
)
def test_bad_numbers_are_rejected(raw: str) -> None:
    with pytest.raises(ValidationError):
        make(whatsapp_number=raw)


@pytest.mark.parametrize("email", ["", "no-at-sign", "a@", "@b.com", "a b@c.com"])
def test_bad_emails_are_rejected(email: str) -> None:
    with pytest.raises(ValidationError):
        make(email=email)


@pytest.mark.parametrize("code", ["", "0", "092", "12345", "9a"])
def test_bad_country_codes_are_rejected(code: str) -> None:
    with pytest.raises(ValidationError):
        make(default_country_code=code)


def test_negative_cap_and_blank_name_rejected() -> None:
    with pytest.raises(ValidationError):
        make(marketing_daily_cap=-1)
    with pytest.raises(ValidationError):
        make(business_name="   ")


def test_unknown_field_rejected_and_empty_optionals_become_none() -> None:
    with pytest.raises(ValidationError):
        make(surprise="x")
    vendor = make(status_webhook_url="", internal_note="")
    assert vendor.status_webhook_url is None
    assert vendor.internal_note is None
    assert vendor.vendor_context == {}


def test_vendor_context_accepts_json_object_or_string() -> None:
    assert make(vendor_context={"clinicId": "gulberg"}).vendor_context == {"clinicId": "gulberg"}
    assert make(vendor_context='{"city": "LHR"}').vendor_context == {"city": "LHR"}
    assert make(vendor_context="").vendor_context == {}
    with pytest.raises(ValidationError):
        make(vendor_context="[1]")
    with pytest.raises(ValidationError):
        make(vendor_context="not json")
    with pytest.raises(ValidationError):
        make(vendor_context={"x": "y" * 9000})
