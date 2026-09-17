"""Add aggregate Delta exposure (DEX).

Revision ID: 0027_delta_exposure
Revises: 0026_quote_unavailable
Create Date: 2026-09-16
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0027_delta_exposure"
down_revision = "0026_quote_unavailable"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add non-null aggregate Delta exposure, initializing historical rows.

    Same pattern as 0007_add_charm_exposure -- a real ThetaData-quoted
    greek (not BSM-derived like gamma/vanna/charm), summed with no
    call/put sign flip: Sigma(delta * open_interest * 100). Delta's own
    sign already distinguishes calls (positive) from puts (negative).
    """
    op.add_column(
        "gamma_aggregates",
        sa.Column(
            "delta_exposure",
            sa.Numeric(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.alter_column(
        "gamma_aggregates",
        "delta_exposure",
        server_default=None,
    )


def downgrade() -> None:
    """Remove aggregate Delta exposure."""
    op.drop_column("gamma_aggregates", "delta_exposure")
