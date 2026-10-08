"""Add whale_alerts.condition_premium: premium of each alert split by OPRA trade-condition code.

Why (2026-10-08): the trade `condition` code (18 auto execution, 125/126
single-leg auction, 130-144 multi-leg, 40-44/148 cancels/corrections, ...)
arrives on every option TRADE message but was discarded when the message was
parsed, and whale_alerts kept no trace of it. So "how much of the alerted
premium was multi-leg?" could not be answered from stored data -- only from a
2-minute raw capture (2026-10-06) and a 1-hour sample (2026-09-15). This
column starts recording it, per alert, as a JSON object
{"<condition code>": <premium in USD>} (code "-1" = the message carried none).
For WHALE/UNUSUAL it covers the alert's one-minute bucket; for SUSTAINED_FLOW
the same 15 minutes as `amount`.

CAPTURE ONLY: no query reads this column and no alert, net-pressure figure or
volume is computed differently because of it (PostgreSQLStorage.
get_recent_whale_alerts keeps its explicit column list).

Nullable, no default, no backfill: a metadata-only change for Postgres, and
existing rows (and BVC-derived ones) simply stay NULL. Same shape as 0038.

Revision ID: 0040_whale_alerts_conditions
Revises: 0039_gamma_near_money_width
Create Date: 2026-10-08
"""

from __future__ import annotations

from alembic import op

revision = "0040_whale_alerts_conditions"
down_revision = "0039_gamma_near_money_width"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE whale_alerts ADD COLUMN IF NOT EXISTS condition_premium jsonb")


def downgrade() -> None:
    op.execute("ALTER TABLE whale_alerts DROP COLUMN IF EXISTS condition_premium")
