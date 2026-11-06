"""futures_events: absorption / stacked imbalance / big trade events from Lester's NinjaTrader ('Convexa Detector', 2026-11-06).

One row per event (a few to a few hundred a day), with the futures price and its translation to the charts it is drawn on (ES -> SPX / SPY,
NQ -> NDX / QQQ, RTY -> IWM) in `translated` (jsonb, symbol -> price). See backend/domain/use_cases/futures_events.py.

Revision ID: 0056_futures_events
Revises: 0055_futures_live
Create Date: 2026-11-06
"""

from __future__ import annotations

from alembic import op

revision = "0056_futures_events"
down_revision = "0055_futures_live"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS futures_events (
            id bigserial PRIMARY KEY,
            future text NOT NULL,
            contract text NOT NULL,
            at timestamptz NOT NULL,
            type text NOT NULL,
            side text NOT NULL,
            price numeric NOT NULL,
            price_low numeric NOT NULL,
            price_high numeric NOT NULL,
            volume integer NOT NULL,
            detail text NOT NULL,
            translated jsonb NOT NULL,
            received_at timestamptz NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS futures_events_future_at ON futures_events (future, at)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS futures_events")
