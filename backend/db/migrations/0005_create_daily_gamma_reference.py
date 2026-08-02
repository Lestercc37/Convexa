"""Create daily reference samples for derived metrics.

Revision ID: 0005_daily_gamma_reference
Revises: 0004_add_spot_price
Create Date: 2026-08-02
"""

from __future__ import annotations

from alembic import op

revision = "0005_daily_gamma_reference"
down_revision = "0004_add_spot_price"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create one fixed-time sample per underlying and trading day."""
    # Samples are captured during 09:35:00-09:35:59 America/New_York.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_gamma_reference (
            date date NOT NULL,
            underlying_id integer NOT NULL REFERENCES underlyings(id),
            net_gamma numeric NOT NULL,
            pc_oi_ratio numeric NOT NULL,
            skew_25d numeric NOT NULL,
            PRIMARY KEY (underlying_id, date)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_daily_gamma_reference_underlying_date
        ON daily_gamma_reference (underlying_id, date DESC)
        """
    )


def downgrade() -> None:
    """Drop daily derived-metric reference samples."""
    op.execute("DROP TABLE IF EXISTS daily_gamma_reference")
