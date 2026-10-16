"""Create large_trade_prints -- the large prints (over $1M) of the proxy ETFs SPY and QQQ with the venue (exchange) and sale condition,
captured by the stream processor from the live stock trade stream.

Why: on 2026-10-16 SPY showed 75% of its shares reported by the FINRA/Nasdaq TRF (exchange 57, off-exchange) and 648 prints over $1M, but most
of the very large ones were average-price (condition 3) or contingent (condition 124) prints at a fixed price ~1% from the quote, not executions at
market. The live code kept neither the exchange nor any print, so that question could only be answered by buying a day of history at a time.
`at_market` marks a print within 0.1% of the last price allowed to set the displayed price (NULL while there is none yet).

Additive: a new table, nothing existing is touched. Written only by the stream processor (QLL_LARGE_TRADE_PRINTS_ENABLED=false switches it off).

Revision ID: 0043_large_trade_prints
Revises: 0042_gamma_item_greeks
Create Date: 2026-10-16
"""

from __future__ import annotations

from alembic import op

revision = "0043_large_trade_prints"
down_revision = "0042_gamma_item_greeks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Keyed on the symbol text like option_quote_minutes (no join needed to write from the stream). time = the exchange timestamp when the
    # message had one, else the receipt time; received_at = when the processor got it.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS large_trade_prints (
            id bigserial PRIMARY KEY,
            time timestamptz NOT NULL,
            symbol text NOT NULL,
            price numeric(14, 4) NOT NULL,
            size bigint NOT NULL,
            condition integer,
            exchange integer,
            at_market boolean,
            received_at timestamptz NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_large_trade_prints_symbol_time ON large_trade_prints (symbol, time DESC)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_large_trade_prints_symbol_time")
    op.execute("DROP TABLE IF EXISTS large_trade_prints")
