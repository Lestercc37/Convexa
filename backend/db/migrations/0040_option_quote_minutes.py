"""Create option_quote_minutes -- one row per contract per minute in which its bid/ask CHANGED, recorded by
the stream processor from the live quote stream (regular session only).

Why: ThetaData's 1-minute quote flat files are Professional-only and limited to the last 7 days, and there
is no stored history of the NBBO per contract finer than the scheduler's cycle snapshots. The stream
already delivers every quote; keeping the last changed bid/ask of each minute gives a minute-resolution
history (the same shape as ThetaData's quote flat file: a minute with no change has no row, carry the
previous row forward) for the gamma heatmap and for recomputing IV/gamma from the mid without ThetaData.

Revision ID: 0040_option_quote_minutes
Revises: 0039_gamma_near_money_width
Create Date: 2026-10-14
"""

from __future__ import annotations

from alembic import op

revision = "0040_option_quote_minutes"
down_revision = "0039_gamma_near_money_width"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Keyed on occ_symbol like contract_cumulative_volume (no FK/join needed to write from the stream).
    # time = the START of the ET minute the quote belongs to; quote_time = when the processor received it.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS option_quote_minutes (
            time timestamptz NOT NULL,
            occ_symbol text NOT NULL,
            bid numeric(14, 4) NOT NULL,
            ask numeric(14, 4) NOT NULL,
            quote_time timestamptz NOT NULL,
            PRIMARY KEY (time, occ_symbol)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_option_quote_minutes_occ_time ON option_quote_minutes (occ_symbol, time DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_option_quote_minutes_occ_time")
    op.execute("DROP TABLE IF EXISTS option_quote_minutes")
