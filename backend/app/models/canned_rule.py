"""canned_rules table: hardcoded replies for the ``canned`` inbound mode (plan sections 5, 7.1)."""

import uuid
from enum import StrEnum

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CannedMatchType(StrEnum):
    TEXT_CONTAINS = "text_contains"  # match_value is a word or phrase, case-insensitive
    MEDIA_TYPE = "media_type"  # match_value is image / sticker / document / ptt / audio
    DEFAULT = "default"  # used when nothing else matched


class CannedRule(Base):
    __tablename__ = "canned_rules"
    __table_args__ = (
        CheckConstraint(
            "match_type IN ('text_contains', 'media_type', 'default')", name="match_type_valid"
        ),
        CheckConstraint("char_length(reply_text) BETWEEN 1 AND 4000", name="reply_text_length"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    vendor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vendors.id", ondelete="CASCADE"), index=True
    )
    priority: Mapped[int] = mapped_column(Integer, default=100, server_default=text("100"))
    match_type: Mapped[str] = mapped_column(String(20))
    match_value: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    reply_text: Mapped[str] = mapped_column(Text)
