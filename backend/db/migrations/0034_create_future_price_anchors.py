"""Create future_price_anchors -- the owner's manually-entered NY session
(9:30 ET) opening print for a future (ES/NQ), read off their own real-time
futures feed (ThinkOrSwim). ES/NQ have no working ThetaData price
stream/OHLC/EOD endpoint at all (see provider.py's own documented gaps),
so their intraday chart/VWAP are synthesized from their cash-index proxy's
own real, already-streaming price history (SPX for ES, NDX for NQ -- see
PRICE_PROXY_SYMBOL_BY_FUTURE) shifted by a constant offset. That offset
only needs one number per session: this anchor minus the proxy's own price
at the same 9:30 ET open. One row per (symbol, session_date) -- entered
once each trading day, overwritable that same day if corrected.

Revision ID: 0034_create_future_price_anchors
Revises: 0033_seed_nq
Create Date: 2026-09-27
"""

from __future__ import annotations

from alembic import op

revision = "0034_create_future_price_anchors"
down_revision = "0033_seed_nq"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS future_price_anchors (
            underlying_id integer NOT NULL REFERENCES underlyings(id),
            session_date date NOT NULL,
            anchor_price numeric NOT NULL,
            updated_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (underlying_id, session_date)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS future_price_anchors")
