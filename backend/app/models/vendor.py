"""vendors table (plan section 5)."""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class VendorStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"


class InboundMode(StrEnum):
    FORWARD = "forward"
    CANNED = "canned"
    ECHO = "echo"
    MOCK_BOT = "mock_bot"
    PAUSED = "paused"


class OutboundMode(StrEnum):
    REAL_PLUGIN = "real_plugin"
    PAUSED = "paused"


def _in_list(column: str, values: type[StrEnum]) -> str:
    quoted = ", ".join(f"'{v.value}'" for v in values)
    return f"{column} IN ({quoted})"


class Vendor(Base):
    __tablename__ = "vendors"
    __table_args__ = (
        CheckConstraint(_in_list("status", VendorStatus), name="status_valid"),
        CheckConstraint(_in_list("inbound_mode", InboundMode), name="inbound_mode_valid"),
        CheckConstraint(_in_list("outbound_mode", OutboundMode), name="outbound_mode_valid"),
        CheckConstraint("marketing_daily_cap >= 0", name="marketing_daily_cap_non_negative"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    business_name: Mapped[str] = mapped_column(String(200))
    phone_e164: Mapped[str] = mapped_column(String(20), index=True)  # digits with country code
    email: Mapped[str] = mapped_column(String(320))

    bot_base_url: Mapped[str | None] = mapped_column(Text)
    bot_secret_enc: Mapped[str] = mapped_column(Text)  # Fernet token, never plain text
    status: Mapped[str] = mapped_column(
        String(20), default=VendorStatus.ACTIVE.value, server_default=VendorStatus.ACTIVE.value
    )
    inbound_mode: Mapped[str] = mapped_column(
        String(20), default=InboundMode.FORWARD.value, server_default=InboundMode.FORWARD.value
    )
    outbound_mode: Mapped[str] = mapped_column(
        String(20),
        default=OutboundMode.REAL_PLUGIN.value,
        server_default=OutboundMode.REAL_PLUGIN.value,
    )

    status_webhook_url: Mapped[str | None] = mapped_column(Text)
    webhook_secret_enc: Mapped[str] = mapped_column(Text)

    default_country_code: Mapped[str] = mapped_column(
        String(4), default="92", server_default=text("'92'")
    )
    marketing_daily_cap: Mapped[int] = mapped_column(
        Integer, default=200, server_default=text("200")
    )
    internal_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
