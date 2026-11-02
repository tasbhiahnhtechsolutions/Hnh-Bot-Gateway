"""api_keys table (plan sections 4 and 5)."""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.security import KeyKind
from app.db.base import Base


class ApiKeyStatus(StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"


class ApiKey(Base):
    __tablename__ = "api_keys"
    __table_args__ = (
        CheckConstraint("kind IN ('live', 'test')", name="kind_valid"),
        CheckConstraint("status IN ('active', 'revoked')", name="status_valid"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    vendor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vendors.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(10), default=KeyKind.LIVE.value)
    key_prefix: Mapped[str] = mapped_column(String(16), unique=True)
    key_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(
        String(10), default=ApiKeyStatus.ACTIVE.value, server_default=ApiKeyStatus.ACTIVE.value
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
