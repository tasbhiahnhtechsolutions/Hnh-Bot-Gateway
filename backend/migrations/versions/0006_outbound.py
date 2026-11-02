"""outbound_jobs, devices, webhook_deliveries

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JOB_STATUSES = "'queued', 'dispatched', 'sending', 'sent', 'failed', 'expired', 'unknown', 'cancelled'"


def upgrade() -> None:
    op.create_table(
        "outbound_jobs",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("vendor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("is_test", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("to_number", sa.String(length=20), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("type", sa.String(length=20), nullable=False),
        sa.Column("priority", sa.SmallInteger(), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="queued", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column(
            "available_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("ttl_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("wa_ack", sa.SmallInteger(), nullable=True),
        sa.Column("error_code", sa.String(length=40), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "type IN ('otp', 'transactional', 'marketing')",
            name=op.f("ck_outbound_jobs_type_valid"),
        ),
        sa.CheckConstraint(
            f"status IN ({JOB_STATUSES})", name=op.f("ck_outbound_jobs_status_valid")
        ),
        sa.ForeignKeyConstraint(
            ["vendor_id"],
            ["vendors.id"],
            name=op.f("fk_outbound_jobs_vendor_id_vendors"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outbound_jobs")),
        sa.UniqueConstraint("vendor_id", "idempotency_key", name="uq_outbound_jobs_idempotency"),
    )
    op.create_index(
        "one_inflight_per_vendor",
        "outbound_jobs",
        ["vendor_id", "is_test"],
        unique=True,
        postgresql_where=sa.text("status IN ('dispatched', 'sending')"),
    )
    op.create_index(
        "ix_outbound_jobs_queue",
        "outbound_jobs",
        ["vendor_id", "is_test", sa.text("priority DESC"), "created_at"],
        postgresql_where=sa.text("status = 'queued'"),
    )
    op.create_index(
        "ix_outbound_jobs_vendor_created", "outbound_jobs", ["vendor_id", "created_at"]
    )

    op.create_table(
        "devices",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("vendor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("device_id", sa.String(length=64), nullable=False),
        sa.Column("wa_number", sa.String(length=20), nullable=True),
        sa.Column("plugin_version", sa.String(length=32), nullable=True),
        sa.Column(
            "last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["vendor_id"],
            ["vendors.id"],
            name=op.f("fk_devices_vendor_id_vendors"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_devices")),
        sa.UniqueConstraint("vendor_id", "device_id", name="uq_devices_vendor_device"),
    )

    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("vendor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", sa.String(length=32), nullable=False),
        sa.Column("event_id", sa.String(length=40), nullable=False),
        sa.Column("event", sa.String(length=30), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("last_status_code", sa.Integer(), nullable=True),
        sa.Column("last_error", sa.String(length=100), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'delivered', 'failed')",
            name=op.f("ck_webhook_deliveries_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["vendor_id"],
            ["vendors.id"],
            name=op.f("fk_webhook_deliveries_vendor_id_vendors"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_webhook_deliveries")),
        sa.UniqueConstraint("event_id", name=op.f("uq_webhook_deliveries_event_id")),
    )
    op.create_index(
        "ix_webhook_deliveries_vendor_created", "webhook_deliveries", ["vendor_id", "created_at"]
    )
    op.create_index(
        "ix_webhook_deliveries_due",
        "webhook_deliveries",
        ["next_attempt_at"],
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_table("webhook_deliveries")
    op.drop_table("devices")
    op.drop_table("outbound_jobs")
