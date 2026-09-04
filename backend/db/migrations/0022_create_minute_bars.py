"""Create closed 1-minute OHLCV bars for the Indices Pro historical backfill.

Revision ID: 0022_minute_bars
Revises: 0021_gamma_flip_nullable
Create Date: 2026-09-04
"""

from __future__ import annotations

from alembic import op

revision = "0022_minute_bars"
down_revision = "0021_gamma_flip_nullable"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """One row per underlying per closed 1-minute bar.

    Backfilled directly from ThetaData's `/v3/index/history/ohlc`
    (Indices Pro plan) by `backend/scripts/backfill_minute_history.py` —
    not written by the live scheduler, unlike `market_snapshots` or
    `daily_bars`.
    """
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS minute_bars (
            time timestamptz NOT NULL,
            underlying_id integer NOT NULL REFERENCES underlyings(id),
            open numeric NOT NULL,
            high numeric NOT NULL,
            low numeric NOT NULL,
            close numeric NOT NULL,
            volume bigint NOT NULL,
            PRIMARY KEY (underlying_id, time)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_minute_bars_underlying_time
        ON minute_bars (underlying_id, time DESC)
        """
    )


def downgrade() -> None:
    """Drop the 1-minute OHLCV backfill table."""
    op.execute("DROP TABLE IF EXISTS minute_bars")
