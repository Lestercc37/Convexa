"""Add aggregate Vanna exposure.

Revision ID: 0011_vanna_exposure
Revises: 0010_seed_active_underlyings
Create Date: 2026-08-06
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011_vanna_exposure"
down_revision = "0010_seed_active_underlyings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add non-null aggregate Vanna exposure, initializing historical rows."""
    op.add_column(
        "gamma_aggregates",
        sa.Column(
            "vanna_exposure",
            sa.Numeric(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.alter_column(
        "gamma_aggregates",
        "vanna_exposure",
        server_default=None,
    )


def downgrade() -> None:
    """Remove aggregate Vanna exposure."""
    op.drop_column("gamma_aggregates", "vanna_exposure")
