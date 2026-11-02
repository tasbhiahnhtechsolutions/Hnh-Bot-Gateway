"""request_logs table: one row per gateway request, metadata only (plan sections 5, 7.2, 14).

Never store message text, files, replies, keys or secrets here.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class RequestKind(StrEnum):
    AI_REPLY = "ai_reply"  # the plugin's real request
    SANDBOX = "sandbox"  # vendor developer, POST /v1/sandbox/simulate-incoming
    LAB = "lab"  # admin Test Lab "Simulate incoming"
    BOT_TEST = "bot_test"  # admin "Test connection"


class RequestStatus(StrEnum):
    OK = "ok"
    ERROR = "error"


class RequestLog(Base):
    __tablename__ = "request_logs"
    __table_args__ = (
        CheckConstraint("kind IN ('ai_reply', 'sandbox', 'lab', 'bot_test')", name="kind_valid"),
        CheckConstraint("status IN ('ok', 'error')", name="status_valid"),
        Index("ix_request_logs_vendor_created", "vendor_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    vendor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vendors.id", ondelete="CASCADE")
    )
    request_id: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(10))
    mode: Mapped[str | None] = mapped_column(String(20))  # inbound mode that handled it
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
