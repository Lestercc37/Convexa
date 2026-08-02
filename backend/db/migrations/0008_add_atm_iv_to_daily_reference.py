"""Add ATM implied volatility to daily reference samples.

Revision ID: 0008_daily_reference_atm_iv
Revises: 0007_charm_exposure
Create Date: 2026-08-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008_daily_reference_atm_iv"
down_revision = "0007_charm_exposure"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add non-null ATM IV, initializing historical rows."""
    op.add_column(
        "daily_gamma_reference",
        sa.Column(
            "atm_iv",
            sa.Numeric(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.alter_column(
        "daily_gamma_reference",
        "atm_iv",
        server_default=None,
    )


def downgrade() -> None:
    """Remove ATM IV from daily reference samples."""
    op.drop_column("daily_gamma_reference", "atm_iv")
