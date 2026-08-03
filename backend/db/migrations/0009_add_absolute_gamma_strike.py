"""Add the absolute Gamma strike to aggregate snapshots.

Revision ID: 0009_absolute_gamma_strike
Revises: 0008_daily_reference_atm_iv
Create Date: 2026-08-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009_absolute_gamma_strike"
down_revision = "0008_daily_reference_atm_iv"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the non-null strike, initializing historical rows."""
    op.add_column(
        "gamma_aggregates",
        sa.Column(
            "absolute_gamma_strike",
            sa.Numeric(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.alter_column(
        "gamma_aggregates",
        "absolute_gamma_strike",
        server_default=None,
    )


def downgrade() -> None:
    """Remove the absolute Gamma strike."""
    op.drop_column("gamma_aggregates", "absolute_gamma_strike")
