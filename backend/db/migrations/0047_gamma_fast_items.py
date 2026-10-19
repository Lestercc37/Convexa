"""Create gamma_fast_items: the per-strike figures of the 10-second "fast gamma layer" (docs/diseno-capa-rapida-gamma-2026-10-19.txt).

Why (2026-10-19, Lester: "2 minutes is an eternity in scalping", then "se puede acelerar el pintado, por ejemplo cada 10 segundos"): the REST cycle stores one snapshot per symbol every ~58 s
and OI, IV and quotes cannot arrive faster, but between cycles only the spot and the clock move. The fast layer re-evaluates the BSM gamma / vanna / charm of the last stored chain at
the live spot every 10 s (measured 2026-10-19 12:06 ET: 0.2 s of CPU for all seven symbols together) and writes ONLY the per-strike rows.

A table of its own, not gamma_aggregate_items: those rows have a foreign key to gamma_aggregates (one parent row per snapshot, the one `latest` reads), and the fast layer must not add
parent rows (nothing that reads the latest aggregate, levels, derived metrics or the screener may change). The heatmap endpoint reads this table when asked for `source=fast`.
Additive, nothing is altered; no backfill. Purge old rows with `DELETE FROM gamma_fast_items WHERE time < now() - interval '7 days'`.

Revision ID: 0047_gamma_fast_items
Revises: 0046_whale_alerts_cube_data
Create Date: 2026-10-19
"""

from __future__ import annotations

from alembic import op

revision = "0047_gamma_fast_items"
down_revision = "0046_whale_alerts_cube_data"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS gamma_fast_items (
            underlying_id integer NOT NULL,
            time timestamptz NOT NULL,
            view text NOT NULL DEFAULT 'tactical',
            strike numeric NOT NULL,
            spot numeric NOT NULL,
            total_gamma_exposure numeric NOT NULL,
            call_gamma_exposure numeric NOT NULL,
            put_gamma_exposure numeric NOT NULL,
            net_gamma numeric NOT NULL,
            contract_count integer NOT NULL,
            absolute_gamma numeric NOT NULL,
            open_interest integer NOT NULL DEFAULT 0,
            volume integer NOT NULL DEFAULT 0,
            delta_exposure numeric,
            vanna_exposure numeric,
            charm_exposure numeric,
            PRIMARY KEY (underlying_id, view, time, strike)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS gamma_fast_items")
