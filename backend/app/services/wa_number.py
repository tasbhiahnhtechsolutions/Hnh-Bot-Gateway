"""Check the plugin runs on the WhatsApp number the vendor registered (plan sections 6, 8.4)."""

import re
from typing import Literal

from app.core.errors import ApiError
from app.core.logging import get_logger
from app.models import Vendor

log = get_logger(__name__)


def digits_only(value: str | None) -> str:
    return re.sub(r"\D", "", value or "")


def check_wa_number(
    vendor: Vendor, header_value: str | None, policy: Literal["warn", "reject"]
) -> None:
    """``reject`` (default) answers 403 NUMBER_MISMATCH; ``warn`` only logs a mismatch."""
    if digits_only(header_value) == vendor.phone_e164:
        return
    if policy == "reject":
        raise ApiError(
            403, "NUMBER_MISMATCH", "this WhatsApp number does not belong to the API key's vendor"
        )
    log.warning(
        "wa_number_mismatch", vendor_id=str(vendor.id), header_present=header_value is not None
    )
