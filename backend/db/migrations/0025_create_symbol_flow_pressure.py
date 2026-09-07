"""Create symbol_flow_pressure -- current net client (aggressor) options
flow, one row per underlying, upserted by the Worker's scheduler cycle
from WhaleAlertsEngine.symbol_flow() (in-memory, fed only by
process_trade()/Lee-Ready). Not a history table like whale_alerts --
only the current snapshot is kept, since the API only ever needs "what
is it right now", not a time series (see GET /flow/{symbol}/pressure).

Revision ID: 0025_symbol_flow_pressure
Revises: 0024_theta_slots
Create Date: 2026-09-07
"""

from __future__ import annotations

from alembic import op

revision = "0025_symbol_flow_pressure"
down_revision = "0024_theta_slots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS symbol_flow_pressure (
            underlying_id integer PRIMARY KEY REFERENCES underlyings(id),
            as_of timestamptz NOT NULL,
            net_call_premium numeric NOT NULL,
            net_put_premium numeric NOT NULL,
            rolling_net_call_premium numeric NOT NULL,
            rolling_net_put_premium numeric NOT NULL,
            rolling_window_minutes integer NOT NULL
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS symbol_flow_pressure")
