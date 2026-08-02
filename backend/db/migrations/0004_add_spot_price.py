"""Persist the chain spot price with each option snapshot.

Revision ID: 0004_add_spot_price
Revises: 0003_theta_vega_not_null
Create Date: 2026-08-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_add_spot_price"
down_revision = "0003_theta_vega_not_null"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the spot captured at the same instant as the option chain."""
    op.add_column(
        "option_chain_snapshots",
        sa.Column(
            "spot_price",
            sa.Numeric(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.alter_column(
        "option_chain_snapshots",
        "spot_price",
        server_default=None,
    )


def downgrade() -> None:
    """Remove persisted chain spot prices."""
    op.drop_column("option_chain_snapshots", "spot_price")
