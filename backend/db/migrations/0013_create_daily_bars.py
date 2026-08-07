"""Create closed daily OHLC bars, the raw material for True Range/ATR.

Revision ID: 0013_daily_bars
Revises: 0012_whale_thresholds
Create Date: 2026-08-07
"""

from __future__ import annotations

from alembic import op

revision = "0013_daily_bars"
down_revision = "0012_whale_thresholds"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create one row per underlying per *closed* trading day.

    Today's in-progress session is never stored here — see
    `calculate_atr_range`, which sources today's open from
    `market_snapshots` instead.
    """
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_bars (
            date date NOT NULL,
            underlying_id integer NOT NULL REFERENCES underlyings(id),
            open numeric NOT NULL,
            high numeric NOT NULL,
            low numeric NOT NULL,
            close numeric NOT NULL,
            PRIMARY KEY (underlying_id, date)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_daily_bars_underlying_date
        ON daily_bars (underlying_id, date DESC)
        """
    )


def downgrade() -> None:
    """Drop closed daily OHLC bars."""
    op.execute("DROP TABLE IF EXISTS daily_bars")
