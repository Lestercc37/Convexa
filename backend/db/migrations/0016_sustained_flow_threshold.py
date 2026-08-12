"""Add the Sustained Flow alert threshold to whale_thresholds.

Revision ID: 0016_sustained_flow_threshold
Revises: 0015_add_future_underlying_kind
Create Date: 2026-08-12
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0016_sustained_flow_threshold"
down_revision = "0015_add_future_underlying_kind"
branch_labels = None
depends_on = None

# Initial calibration, not final — same "needs recalibration with real
# data" caveat as the other four columns in this table (docs/use-cases.md).
_DEFAULT_SUSTAINED_FLOW_MIN = 500000


def upgrade() -> None:
    """Add the column and backfill every existing row with the default."""
    op.execute(
        """
        ALTER TABLE whale_thresholds
        ADD COLUMN sustained_flow_min numeric
        """
    )
    connection = op.get_bind()
    connection.execute(
        text("UPDATE whale_thresholds SET sustained_flow_min = :default"),
        {"default": _DEFAULT_SUSTAINED_FLOW_MIN},
    )
    op.execute(
        """
        ALTER TABLE whale_thresholds
        ALTER COLUMN sustained_flow_min SET NOT NULL
        """
    )


def downgrade() -> None:
    """Drop the column."""
    op.execute("ALTER TABLE whale_thresholds DROP COLUMN sustained_flow_min")
