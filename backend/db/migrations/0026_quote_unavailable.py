"""Add whale_alerts.quote_unavailable, distinguishing "no quote yet" from
a genuine tied split.

Revision ID: 0026_quote_unavailable
Revises: 0025_symbol_flow_pressure
Create Date: 2026-09-08
"""

from __future__ import annotations

from alembic import op

revision = "0026_quote_unavailable"
down_revision = "0025_symbol_flow_pressure"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """estimated_buy_volume == estimated_sell_volume today means one of
    two very different things, indistinguishable until now: Lee-Ready
    had no bid/ask to classify the trade against (Side.UNKNOWN,
    calculate_lee_ready.py) and process_trade() fell back to a neutral
    50/50 split of a real premium, or -- for process()/BVC-derived
    alerts, which this column is always False for -- a genuine
    coincidental tie. Confirmed live, 2026-09 (real SPX 0DTE, market
    open): the former was ~94% of SPX's own alerts in a 30-minute
    window, previously all shown identically as "Mixto".

    DEFAULT false, not nullable: every pre-existing row predates this
    distinction entirely, and there's no way to reconstruct which of the
    two it actually was after the fact -- false (i.e. "assume it was a
    real split, same as it displayed before this migration") is the
    honest choice for history, not a guess of "true" for something never
    tracked.
    """
    op.execute(
        """
        ALTER TABLE whale_alerts
        ADD COLUMN IF NOT EXISTS quote_unavailable boolean NOT NULL DEFAULT false
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE whale_alerts DROP COLUMN IF EXISTS quote_unavailable")
