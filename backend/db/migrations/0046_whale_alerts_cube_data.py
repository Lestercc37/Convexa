"""Add whale_alerts.delta and whale_alerts.contracts: what the heatmap's alert cubes need to be sized and filtered.

Why (2026-10-17, heatmap design 12.8-12.10): the alert cubes drawn on the price chart are sized and filtered by "delta-equivalent
contracts" = contracts x |delta|, and the alert stored only its premium. This records, per alert and when it fires:
- delta: the contract's |delta| from its latest stored chain snapshot (the same read that tags ITM / ATM / OTM / FAR), NULL when none was
  stored. Magnitude only: whether it is a call or a put is in the contract symbol.
- contracts: the contracts traded in the alert's window (for WHALE/UNUSUAL the one-minute bucket, for SUSTAINED_FLOW the same 15 minutes as
  `amount`), counting every trade of the contract whatever its condition code. NULL for rows written before this migration and for
  BVC-derived alerts, which never see trade sizes.
Nullable, no default, no backfill: a metadata-only change for Postgres, same shape as 0041 and 0045. Unlike those two captures, these columns are
READ by the alerts endpoint.

Revision ID: 0046_whale_alerts_cube_data
Revises: 0045_whale_alerts_simple_flow
Create Date: 2026-10-17
"""

from __future__ import annotations

from alembic import op

revision = "0046_whale_alerts_cube_data"
down_revision = "0045_whale_alerts_simple_flow"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Brief ACCESS EXCLUSIVE lock for the catalog change; give up after 5 s instead of queueing every insert/select behind a long
    # transaction (SET LOCAL: only this migration's transaction). A failed run changes nothing and can be repeated.
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("ALTER TABLE whale_alerts ADD COLUMN IF NOT EXISTS delta numeric, ADD COLUMN IF NOT EXISTS contracts integer")


def downgrade() -> None:
    op.execute("ALTER TABLE whale_alerts DROP COLUMN IF EXISTS contracts, DROP COLUMN IF EXISTS delta")
