"""canned_rules

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "canned_rules",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("vendor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("priority", sa.Integer(), server_default=sa.text("100"), nullable=False),
        sa.Column("match_type", sa.String(length=20), nullable=False),
        sa.Column("match_value", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("reply_text", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "match_type IN ('text_contains', 'media_type', 'default')",
            name=op.f("ck_canned_rules_match_type_valid"),
        ),
        sa.CheckConstraint(
            "char_length(reply_text) BETWEEN 1 AND 4000",
            name=op.f("ck_canned_rules_reply_text_length"),
        ),
        sa.ForeignKeyConstraint(
            ["vendor_id"],
            ["vendors.id"],
            name=op.f("fk_canned_rules_vendor_id_vendors"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_canned_rules")),
    )
    op.create_index(op.f("ix_canned_rules_vendor_id"), "canned_rules", ["vendor_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_canned_rules_vendor_id"), table_name="canned_rules")
    op.drop_table("canned_rules")
