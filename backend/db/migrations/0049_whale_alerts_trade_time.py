"""Add whale_alerts.trade_start and whale_alerts.trade_minutes: WHEN the trades behind an alert happened.

Why (2026-10-20, Lester): the alert's own time (`time`) is when the alert was issued, which is when its minute bucket closed (the next trade of the contract, or the flush timer),
not when the trades happened. He wants the transaction time in the alert cube's hover card.
- trade_start: start of the ET minute the alert's trades belong to (WHALE / UNUSUAL: the one-minute bucket; SUSTAINED_FLOW: the start of its 15-minute window).
- trade_minutes: how many minutes that window spans (1 or 15).
Nullable, no default, no backfill (rows written before this read back as NULL and the card keeps showing only the alert time): a metadata-only change, same shape as 0046.

Revision ID: 0049_whale_alerts_trade_time
Revises: 0048_minute_option_flow
Create Date: 2026-10-20
"""

from __future__ import annotations

from alembic import op

revision = "0049_whale_alerts_trade_time"
down_revision = "0048_minute_option_flow"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("ALTER TABLE whale_alerts ADD COLUMN IF NOT EXISTS trade_start timestamptz, ADD COLUMN IF NOT EXISTS trade_minutes smallint")


def downgrade() -> None:
    op.execute("ALTER TABLE whale_alerts DROP COLUMN IF EXISTS trade_minutes, DROP COLUMN IF EXISTS trade_start")
