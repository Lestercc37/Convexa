"""Make Theta and Vega mandatory in option chain snapshots.

Revision ID: 0003_theta_vega_not_null
Revises: 0002_add_charm_vanna
Create Date: 2026-08-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_theta_vega_not_null"
down_revision = "0002_add_charm_vanna"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Backfill existing rows before enforcing mandatory Greeks."""
    op.execute(
        "UPDATE option_chain_snapshots SET theta = 0 WHERE theta IS NULL"
    )
    op.execute(
        "UPDATE option_chain_snapshots SET vega = 0 WHERE vega IS NULL"
    )
    op.alter_column(
        "option_chain_snapshots",
        "theta",
        existing_type=sa.Numeric(),
        nullable=False,
    )
    op.alter_column(
        "option_chain_snapshots",
        "vega",
        existing_type=sa.Numeric(),
        nullable=False,
    )


def downgrade() -> None:
    """Restore the original nullable constraints."""
    op.alter_column(
        "option_chain_snapshots",
        "vega",
        existing_type=sa.Numeric(),
        nullable=True,
    )
    op.alter_column(
        "option_chain_snapshots",
        "theta",
        existing_type=sa.Numeric(),
        nullable=True,
    )
