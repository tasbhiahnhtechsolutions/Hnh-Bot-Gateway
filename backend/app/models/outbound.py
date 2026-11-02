"""Outbound tables: jobs, devices, webhook deliveries (plan sections 5, 8)."""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class JobType(StrEnum):
    OTP = "otp"
    TRANSACTIONAL = "transactional"
    MARKETING = "marketing"


class JobStatus(StrEnum):
    QUEUED = "queued"
    DISPATCHED = "dispatched"  # handed to the plugin, no "started" yet
    SENDING = "sending"  # plugin said "started": the message may already be on its way
    SENT = "sent"
    FAILED = "failed"
    EXPIRED = "expired"
    UNKNOWN = "unknown"  # we cannot tell whether it was sent. Never retried automatically
    CANCELLED = "cancelled"


class WebhookStatus(StrEnum):
    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"


def _in_list(column: str, values: type[StrEnum]) -> str:
    quoted = ", ".join(f"'{v.value}'" for v in values)
    return f"{column} IN ({quoted})"


class OutboundJob(Base):
    __tablename__ = "outbound_jobs"
    __table_args__ = (
        CheckConstraint(_in_list("type", JobType), name="type_valid"),
        CheckConstraint(_in_list("status", JobStatus), name="status_valid"),
        UniqueConstraint("vendor_id", "idempotency_key", name="uq_outbound_jobs_idempotency"),
        # One job per vendor "in the air" at a time (plan section 5). The real and the virtual
        # (test key) plugin are different lines, so the rule holds per (vendor, is_test).
        Index(
            "one_inflight_per_vendor",
            "vendor_id",
            "is_test",
            unique=True,
            postgresql_where=text("status IN ('dispatched', 'sending')"),
        ),
        Index(
            "ix_outbound_jobs_queue",
            "vendor_id",
            "is_test",
            text("priority DESC"),
            "created_at",
            postgresql_where=text("status = 'queued'"),
        ),
        Index("ix_outbound_jobs_vendor_created", "vendor_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # job_<16 hex>
    vendor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vendors.id", ondelete="CASCADE")
    )
    is_test: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    to_number: Mapped[str] = mapped_column(String(20))
    body: Mapped[str | None] = mapped_column(Text)  # wiped 24h after the final status
    type: Mapped[str] = mapped_column(String(20))
    priority: Mapped[int] = mapped_column(SmallInteger)
    status: Mapped[str] = mapped_column(
        String(20), default=JobStatus.QUEUED.value, server_default=JobStatus.QUEUED.value
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    ttl_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    wa_ack: Mapped[int | None] = mapped_column(SmallInteger)
    error_code: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Device(Base):
    """One Chrome install that connected with a vendor's live key."""

    __tablename__ = "devices"
    __table_args__ = (UniqueConstraint("vendor_id", "device_id", name="uq_devices_vendor_device"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    vendor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vendors.id", ondelete="CASCADE")
    )
    device_id: Mapped[str] = mapped_column(String(64))
    wa_number: Mapped[str | None] = mapped_column(String(20))
    plugin_version: Mapped[str | None] = mapped_column(String(32))
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"
    __table_args__ = (
        CheckConstraint(_in_list("status", WebhookStatus), name="status_valid"),
        Index("ix_webhook_deliveries_vendor_created", "vendor_id", "created_at"),
        Index(
            "ix_webhook_deliveries_due",
            "next_attempt_at",
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    vendor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vendors.id", ondelete="CASCADE")
    )
    job_id: Mapped[str] = mapped_column(String(32))
    event_id: Mapped[str] = mapped_column(String(40), unique=True)
    event: Mapped[str] = mapped_column(String(30))
    payload: Mapped[dict[str, object]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(
        String(20), default=WebhookStatus.PENDING.value, server_default="pending"
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_status_code: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
