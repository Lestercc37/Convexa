"""index_price_fallback: SPX / NDX replaced by SPY / QQQ while ThetaData's index feed lags.

Why (2026-11-06): from ~09:37 ET ThetaData delivered every index 8-10 minutes late while stocks and options were on time; Convexa's SPX / NDX
(and so ES / NQ, the fast gamma layer and the 0DTE heatmap) ran 15-34 points behind. The stream processor now replaces a lagging index by its
proxy x the last good ratio (backend/domain/use_cases/index_stale_guard.py) and keeps one row per index here, so the dashboard can say so.
One row per index, rewritten in place: a few writes a minute while an episode is open, nothing otherwise.

Revision ID: 0054_index_price_fallback
Revises: 0053_future_anchor_source
Create Date: 2026-11-06
"""

from __future__ import annotations

from alembic import op

revision = "0054_index_price_fallback"
down_revision = "0053_future_anchor_source"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS index_price_fallback (
            symbol text PRIMARY KEY,
            proxy text NOT NULL,
            active boolean NOT NULL,
            ratio double precision NOT NULL,
            reason text NOT NULL,
            since timestamptz NOT NULL,
            updated_at timestamptz NOT NULL
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS index_price_fallback")
