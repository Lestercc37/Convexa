"""futures_live_quotes + futures_live_bars: live ES / NQ / RTY from Lester's NinjaTrader ('Convexa Feed' indicator, 2026-11-06).

futures_live_quotes keeps ONE row per future (the last price, rewritten about once a second); futures_live_bars one row per closed 1-minute bar
and contract (~780 a day per future with the evening session), with the index's last price inside the same minute when it had a fresh one:
the live basis (backend/domain/use_cases/futures_live.py) is close - index_price. CME real-time data: the raw prices are shown only to the
owner (see that module).

Revision ID: 0055_futures_live
Revises: 0054_index_price_fallback
Create Date: 2026-11-06
"""

from __future__ import annotations

from alembic import op

revision = "0055_futures_live"
down_revision = "0054_index_price_fallback"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS futures_live_quotes (
            symbol text PRIMARY KEY,
            contract text NOT NULL,
            last numeric NOT NULL,
            bid numeric,
            ask numeric,
            exchange_time timestamptz NOT NULL,
            received_at timestamptz NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS futures_live_bars (
            symbol text NOT NULL,
            contract text NOT NULL,
            minute timestamptz NOT NULL,
            open numeric NOT NULL,
            high numeric NOT NULL,
            low numeric NOT NULL,
            close numeric NOT NULL,
            volume bigint NOT NULL,
            session text NOT NULL,
            index_price numeric,
            received_at timestamptz NOT NULL,
            PRIMARY KEY (symbol, contract, minute)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS futures_live_bars_symbol_minute ON futures_live_bars (symbol, minute)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS futures_live_bars")
    op.execute("DROP TABLE IF EXISTS futures_live_quotes")
