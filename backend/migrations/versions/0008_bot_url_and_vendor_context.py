"""Full inbound bot URL (no /ai-reply append) and vendor_context JSON.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "vendors",
        sa.Column(
            "vendor_context",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    # Rows stored a host/origin; we used to POST {url}/ai-reply. Keep those working.
    op.execute(
        """
        UPDATE vendors
        SET bot_base_url = regexp_replace(bot_base_url, '/+$', '') || '/ai-reply'
        WHERE bot_base_url IS NOT NULL
          AND bot_base_url ~ '^https?://[^/?#]+(:[0-9]+)?/?$'
        """
    )


def downgrade() -> None:
    op.drop_column("vendors", "vendor_context")
