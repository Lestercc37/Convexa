"""future_price_anchors gets a `source`: who saved the day's ES/NQ opening anchor.

Why (Lester 2026-11-02 / 11-04): the anchor is now also saved automatically, from his NinjaTrader at 09:31 (indicator 'Convexa Ancla') or from
the 09:41 Yahoo task, and an automatic save must never overwrite a number he typed himself. 'manual' = typed in the app (NULL on older rows
means the same: before this change only he could write it), 'ninjatrader' / 'yahoo' = automatic. Nullable, no default: catalog change only.

Revision ID: 0053_future_anchor_source
Revises: 0052_option_quote_minute_range
Create Date: 2026-11-04
"""

from __future__ import annotations

from alembic import op

revision = "0053_future_anchor_source"
down_revision = "0052_option_quote_minute_range"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE future_price_anchors ADD COLUMN IF NOT EXISTS source text")


def downgrade() -> None:
    op.execute("ALTER TABLE future_price_anchors DROP COLUMN IF EXISTS source")
