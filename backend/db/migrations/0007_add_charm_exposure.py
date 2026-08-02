"""Add aggregate Charm exposure.

Revision ID: 0007_charm_exposure
Revises: 0006_vega_theta_exposure
Create Date: 2026-08-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_charm_exposure"
down_revision = "0006_vega_theta_exposure"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add non-null aggregate Charm exposure, initializing historical rows."""
    op.add_column(
        "gamma_aggregates",
        sa.Column(
            "charm_exposure",
            sa.Numeric(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.alter_column(
        "gamma_aggregates",
        "charm_exposure",
        server_default=None,
    )


def downgrade() -> None:
    """Remove aggregate Charm exposure."""
    op.drop_column("gamma_aggregates", "charm_exposure")
