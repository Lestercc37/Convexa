"""Add mandatory Charm and Vanna Greeks to option chain snapshots.

Revision ID: 0002_add_charm_vanna
Revises: 0001_initial_schema
Create Date: 2026-08-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_add_charm_vanna"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Persist the two Greeks that became mandatory in the MVP."""
    op.add_column(
        "option_chain_snapshots",
        sa.Column(
            "charm",
            sa.Numeric(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "option_chain_snapshots",
        sa.Column(
            "vanna",
            sa.Numeric(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.alter_column("option_chain_snapshots", "charm", server_default=None)
    op.alter_column("option_chain_snapshots", "vanna", server_default=None)


def downgrade() -> None:
    """Remove Charm and Vanna from persisted option snapshots."""
    op.drop_column("option_chain_snapshots", "vanna")
    op.drop_column("option_chain_snapshots", "charm")
