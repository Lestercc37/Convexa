"""Create the per-strike breakdown persisted alongside each GammaAggregate.

Revision ID: 0014_gamma_aggregate_items
Revises: 0013_daily_bars
Create Date: 2026-08-10
"""

from __future__ import annotations

from alembic import op

revision = "0014_gamma_aggregate_items"
down_revision = "0013_daily_bars"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Persist `GammaAggregate.items`, dropped on every prior save.

    The per-strike breakdown was already computed on every calculation
    cycle (CalculateGammaExposureOrchestrator) but discarded before
    `save_gamma_aggregate` — this is not a new calculation, only a new
    place to keep a value the engine already produces. `gamma_aggregates`
    has no surrogate key, so a real FK requires giving it one first; its
    natural key is `(underlying_id, time)`.
    """
    op.execute(
        """
        ALTER TABLE gamma_aggregates
        ADD CONSTRAINT gamma_aggregates_pkey PRIMARY KEY (underlying_id, time)
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS gamma_aggregate_items (
            underlying_id integer NOT NULL,
            time timestamptz NOT NULL,
            strike numeric NOT NULL,
            total_gamma_exposure numeric NOT NULL,
            call_gamma_exposure numeric NOT NULL,
            put_gamma_exposure numeric NOT NULL,
            net_gamma numeric NOT NULL,
            contract_count integer NOT NULL,
            absolute_gamma numeric NOT NULL,
            open_interest integer NOT NULL DEFAULT 0,
            volume integer NOT NULL DEFAULT 0,
            PRIMARY KEY (underlying_id, time, strike),
            FOREIGN KEY (underlying_id, time)
                REFERENCES gamma_aggregates (underlying_id, time)
        )
        """
    )


def downgrade() -> None:
    """Drop the per-strike breakdown and the primary key it depends on."""
    op.execute("DROP TABLE IF EXISTS gamma_aggregate_items")
    op.execute(
        """
        ALTER TABLE gamma_aggregates
        DROP CONSTRAINT IF EXISTS gamma_aggregates_pkey
        """
    )
