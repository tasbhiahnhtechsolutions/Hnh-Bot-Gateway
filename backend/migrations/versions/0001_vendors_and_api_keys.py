"""vendors and api_keys

Revision ID: 0001
Revises:
Create Date: 2026-11-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "vendors",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("business_name", sa.String(length=200), nullable=False),
        sa.Column("phone_e164", sa.String(length=20), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("bot_base_url", sa.Text(), nullable=True),
        sa.Column("bot_secret_enc", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="active", nullable=False),
        sa.Column("inbound_mode", sa.String(length=20), server_default="forward", nullable=False),
        sa.Column(
            "outbound_mode", sa.String(length=20), server_default="real_plugin", nullable=False
        ),
        sa.Column("status_webhook_url", sa.Text(), nullable=True),
        sa.Column("webhook_secret_enc", sa.Text(), nullable=False),
        sa.Column("default_country_code", sa.String(length=4), server_default="92", nullable=False),
        sa.Column("marketing_daily_cap", sa.Integer(), server_default="200", nullable=False),
        sa.Column("internal_note", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('active', 'suspended')", name=op.f("ck_vendors_status_valid")
        ),
        sa.CheckConstraint(
            "inbound_mode IN ('forward', 'canned', 'echo', 'mock_bot', 'paused')",
            name=op.f("ck_vendors_inbound_mode_valid"),
        ),
        sa.CheckConstraint(
            "outbound_mode IN ('real_plugin', 'paused')",
            name=op.f("ck_vendors_outbound_mode_valid"),
        ),
        sa.CheckConstraint(
            "marketing_daily_cap >= 0", name=op.f("ck_vendors_marketing_daily_cap_non_negative")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_vendors")),
    )
    op.create_index(op.f("ix_vendors_phone_e164"), "vendors", ["phone_e164"])

    op.create_table(
        "api_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("vendor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("key_prefix", sa.String(length=16), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=10), server_default="active", nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("kind IN ('live', 'test')", name=op.f("ck_api_keys_kind_valid")),
        sa.CheckConstraint(
            "status IN ('active', 'revoked')", name=op.f("ck_api_keys_status_valid")
        ),
        sa.ForeignKeyConstraint(
            ["vendor_id"],
            ["vendors.id"],
            name=op.f("fk_api_keys_vendor_id_vendors"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_api_keys")),
        sa.UniqueConstraint("key_prefix", name=op.f("uq_api_keys_key_prefix")),
    )
    op.create_index(op.f("ix_api_keys_vendor_id"), "api_keys", ["vendor_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_api_keys_vendor_id"), table_name="api_keys")
    op.drop_table("api_keys")
    op.drop_index(op.f("ix_vendors_phone_e164"), table_name="vendors")
    op.drop_table("vendors")
