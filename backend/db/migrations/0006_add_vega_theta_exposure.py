"""Add aggregate Vega and Theta exposure.

Revision ID: 0006_vega_theta_exposure
Revises: 0005_daily_gamma_reference
Create Date: 2026-08-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006_vega_theta_exposure"
down_revision = "0005_daily_gamma_reference"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add non-null aggregate exposures, initializing historical rows."""
    for column_name in ("vega_exposure", "theta_exposure"):
        op.add_column(
            "gamma_aggregates",
            sa.Column(
                column_name,
                sa.Numeric(),
                nullable=False,
                server_default=sa.text("0"),
            ),
        )
        op.alter_column(
            "gamma_aggregates",
            column_name,
            server_default=None,
        )


def downgrade() -> None:
    """Remove aggregate Vega and Theta exposure."""
    op.drop_column("gamma_aggregates", "theta_exposure")
    op.drop_column("gamma_aggregates", "vega_exposure")
