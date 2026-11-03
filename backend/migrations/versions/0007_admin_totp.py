"""admins.totp_secret_enc for two-factor login (plan section 9.3)

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("admins", sa.Column("totp_secret_enc", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("admins", "totp_secret_enc")
