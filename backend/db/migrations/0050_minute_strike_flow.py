"""Create minute_strike_flow: the signed option flow per ET minute, per strike (docs/diseno-cadena-flujo-2026-10-25.txt, section 3; Lester approved the flow chain 2026-10-26).

Why: the flow chain view (his gex-flow.html table: Flujo 5M / 15M / 30M / 60M per strike, packages apart, per-strike alerts) needs the REAL flow that enters each strike. minute_option_flow
(0048) keeps one row per symbol and root, so the strike is lost; nothing else stores it.

One row per (minute, symbol, expiration, strike, right): `delta_flow` in delta-equivalent contracts (contracts x |delta|), sign buying a call +, selling a call -, buying a put -, selling a put
+ (positive = bullish pressure), from simple trades (OPRA condition 18) signed against the prevailing bid/ask, the same rule as minute_option_flow. The contracts are split three ways and add up
to the volume of the cell: `contracts_signed` (carry a side), `contracts_package` (multi-leg legs, codes 130-144, shown apart, never as "no side"), `contracts_other` (inside the spread, no
quote / delta yet, auctions, crosses, floor...). `buy_premium` / `sell_premium` are the dollars of the signed trades.

Size: about 120k-200k rows a day for every symbol and expiration the stream follows (SPX 0DTE alone had 37,790 cells on 2026-10-23), 20-30 MB a day.

Additive: a new table, nothing existing is touched. Written only by the stream processor and only with QLL_MINUTE_STRIKE_FLOW_ENABLED=true (off by default, so the code can be deployed
before this migration is applied).

Revision ID: 0050_minute_strike_flow
Revises: 0049_whale_alerts_trade_time
Create Date: 2026-10-29
"""

from __future__ import annotations

from alembic import op

revision = "0050_minute_strike_flow"
down_revision = "0049_whale_alerts_trade_time"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS minute_strike_flow (
            minute timestamptz NOT NULL,
            symbol text NOT NULL,
            expiration date NOT NULL,
            strike numeric(12, 3) NOT NULL,
            option_right char(1) NOT NULL,
            delta_flow numeric(18, 4) NOT NULL DEFAULT 0,
            contracts_signed integer NOT NULL DEFAULT 0,
            contracts_package integer NOT NULL DEFAULT 0,
            contracts_other integer NOT NULL DEFAULT 0,
            buy_premium numeric(18, 2) NOT NULL DEFAULT 0,
            sell_premium numeric(18, 2) NOT NULL DEFAULT 0,
            PRIMARY KEY (symbol, minute, expiration, strike, option_right)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS minute_strike_flow")
