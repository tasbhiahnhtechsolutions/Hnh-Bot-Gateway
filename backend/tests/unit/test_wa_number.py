import uuid

import pytest

from app.core.errors import ApiError
from app.models import Vendor
from app.services.wa_number import check_wa_number, digits_only

VENDOR = Vendor(id=uuid.uuid4(), phone_e164="923001234567")


@pytest.mark.parametrize("header", ["923001234567", "+92 300 1234567", "+923001234567"])
def test_matching_numbers_pass_in_both_policies(header: str) -> None:
    check_wa_number(VENDOR, header, "reject")
    check_wa_number(VENDOR, header, "warn")


@pytest.mark.parametrize("header", [None, "", "923009999999", "garbage"])
def test_mismatch_is_rejected_when_strict(header: str | None) -> None:
    with pytest.raises(ApiError) as info:
        check_wa_number(VENDOR, header, "reject")
    assert info.value.status_code == 403
    assert info.value.code == "NUMBER_MISMATCH"


@pytest.mark.parametrize("header", [None, "", "923009999999"])
def test_mismatch_only_warns_by_default(header: str | None) -> None:
    check_wa_number(VENDOR, header, "warn")


def test_digits_only() -> None:
    assert digits_only("+92 (300) 123-4567") == "923001234567"
    assert digits_only(None) == ""
