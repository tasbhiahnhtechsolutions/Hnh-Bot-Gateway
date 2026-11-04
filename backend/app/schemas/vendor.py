"""Input for creating a vendor (plan section 9.1 onboarding fields and their rules)."""

import json
import re
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints, field_validator

from app.models.vendor import InboundMode, OutboundMode

VENDOR_CONTEXT_MAX_BYTES = 8192

_NUMBER_NOISE = re.compile(r"[\s\-()]")

NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class VendorCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    business_name: NonEmptyStr = Field(max_length=200)
    whatsapp_number: str
    email: EmailStr
    bot_base_url: NonEmptyStr  # full inbound URL; the gateway POSTs exactly this
    vendor_context: dict[str, Any] = Field(default_factory=dict)
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

    @field_validator("vendor_context", mode="before")
    @classmethod
    def _parse_vendor_context(cls, value: object) -> dict[str, Any]:
        if value is None or value == "":
            return {}
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError as exc:
                raise ValueError("must be a JSON object") from exc
        if not isinstance(value, dict):
            raise ValueError("must be a JSON object")
        if any(not isinstance(key, str) for key in value):
            raise ValueError("must be a JSON object")
        packed = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
        if len(packed) > VENDOR_CONTEXT_MAX_BYTES:
            raise ValueError(f"must be at most {VENDOR_CONTEXT_MAX_BYTES} bytes")
        return value
