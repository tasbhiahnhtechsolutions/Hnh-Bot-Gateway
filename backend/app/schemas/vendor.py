"""Input for creating a vendor (plan section 9.1 onboarding fields and their rules)."""

import re
from typing import Annotated

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints, field_validator

from app.models.vendor import InboundMode, OutboundMode

_NUMBER_NOISE = re.compile(r"[\s\-()]")

NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class VendorCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    business_name: NonEmptyStr = Field(max_length=200)
    whatsapp_number: str
    email: EmailStr
    bot_base_url: NonEmptyStr
    status_webhook_url: str | None = None
    default_country_code: str = "92"
    marketing_daily_cap: int = Field(default=200, ge=0)
    inbound_mode: InboundMode = InboundMode.FORWARD
    outbound_mode: OutboundMode = OutboundMode.REAL_PLUGIN
    internal_note: str | None = None

    @field_validator("whatsapp_number")
    @classmethod
    def _normalize_number(cls, value: str) -> str:
        """Digits with country code, 10 to 15 long. ``+``, spaces and dashes are tolerated."""
        cleaned = _NUMBER_NOISE.sub("", value).removeprefix("+")
        if not cleaned.isascii() or not cleaned.isdigit() or not 10 <= len(cleaned) <= 15:
            raise ValueError("must be 10 to 15 digits including the country code")
        return cleaned

    @field_validator("default_country_code")
    @classmethod
    def _check_country_code(cls, value: str) -> str:
        if not re.fullmatch(r"[1-9][0-9]{0,3}", value):
            raise ValueError("must be 1 to 4 digits and must not start with 0")
        return value

    @field_validator("status_webhook_url", "internal_note")
    @classmethod
    def _empty_to_none(cls, value: str | None) -> str | None:
        return value or None
