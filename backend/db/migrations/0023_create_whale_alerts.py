"""Create whale_alerts, persisting WhaleAlertsEngine's alert history.

Revision ID: 0023_whale_alerts
Revises: 0022_minute_bars
Create Date: 2026-09-04
"""

from __future__ import annotations

from alembic import op

revision = "0023_whale_alerts"
down_revision = "0022_minute_bars"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """One row per detected WHALE/UNUSUAL/SUSTAINED_FLOW alert.

    Append-only, no row limit -- same pattern as market_snapshots/
    flow_events, not the in-memory deque(maxlen=1000) WhaleAlertsEngine
    still also writes to in this phase (dual-write, see its own
    docstring). `occ_symbol` is plain text, not a contract_id FK: a
    whale trade can land on a contract outside the near-the-money
    range option_chain_snapshots covers, so there's no guarantee
    option_contracts already has a row for it.
    """
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS whale_alerts (
            time timestamptz NOT NULL,
            underlying_id integer NOT NULL REFERENCES underlyings(id),
            occ_symbol text NOT NULL,
            alert_type text NOT NULL CHECK (alert_type IN ('WHALE', 'UNUSUAL', 'SUSTAINED_FLOW')),
            amount numeric NOT NULL,
            estimated_buy_volume numeric NOT NULL,
            estimated_sell_volume numeric NOT NULL
        )
        """
    )
    _create_hypertable_if_available("whale_alerts")
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_whale_alerts_underlying_time
        ON whale_alerts (underlying_id, time DESC)
        """
    )


def _create_hypertable_if_available(table_name: str) -> None:
    """Convert a table only when TimescaleDB is installed in this database."""
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'
            ) THEN
                PERFORM create_hypertable(
                    '{table_name}', 'time', if_not_exists => TRUE
                );
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS whale_alerts")
