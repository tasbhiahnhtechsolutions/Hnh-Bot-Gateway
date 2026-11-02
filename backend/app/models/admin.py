"""admins and audit_log tables (plan sections 5 and 9.3)."""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AdminRole(StrEnum):
    SUPERADMIN = "superadmin"  # everything
    SUPPORT = "support"  # look at things and use the Test Lab; no changes, no keys


class Admin(Base):
    __tablename__ = "admins"
    __table_args__ = (CheckConstraint("role IN ('superadmin', 'support')", name="role_valid"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True)  # stored lower-case
    password_hash: Mapped[str] = mapped_column(Text)  # argon2id
    role: Mapped[str] = mapped_column(String(20))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditLog(Base):
    """Who did what. ``target`` never holds a key, secret, password or message text."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    admin_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admins.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String(50))
    target: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
