"""Create minute_option_flow: the signed delta flow of option trades, per ET minute, per underlying and root (docs/diseno-heatmap-gamma-2026-10-14.txt, ADENDA 4, 12.3).

Why (2026-10-19, Lester): he wants the 1-minute calls / puts delta-flow bars under the candles AND the history to test whether incoming flow, together with gamma, IV, OI and volume, says anything
(the 15-minute check of the raw delta exposure found nothing: it mirrors the price). No per-minute flow was stored anywhere (flow_events is empty, symbol_flow_pressure keeps only the latest), so
this table starts accumulating that history from the day it is deployed.

One row per (minute, symbol, root): `calls_delta_flow` / `puts_delta_flow` in delta-equivalent contracts (contracts x |delta|), sign convention buying a call +, selling a call -, buying a put -,
selling a put + (positive = bullish pressure on both sides). Only simple trades (OPRA condition 18) classified against the prevailing bid/ask count as flow; everything else is counted in
`trades_unclassified` so the share of volume that carries a side is always visible (`contracts_classified` / `contracts_total`).

Additive: a new table, nothing existing is touched. Written only by the stream processor (QLL_MINUTE_OPTION_FLOW_ENABLED=false switches it off).

Revision ID: 0048_minute_option_flow
Revises: 0047_gamma_fast_items
Create Date: 2026-10-19
"""

from __future__ import annotations

from alembic import op

revision = "0048_minute_option_flow"
down_revision = "0047_gamma_fast_items"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS minute_option_flow (
            minute timestamptz NOT NULL,
            symbol text NOT NULL,
            root text NOT NULL,
            calls_delta_flow numeric(18, 4) NOT NULL DEFAULT 0,
            puts_delta_flow numeric(18, 4) NOT NULL DEFAULT 0,
            trades_classified integer NOT NULL DEFAULT 0,
            trades_unclassified integer NOT NULL DEFAULT 0,
            contracts_classified integer NOT NULL DEFAULT 0,
            contracts_total integer NOT NULL DEFAULT 0,
            PRIMARY KEY (symbol, minute, root)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS minute_option_flow")
