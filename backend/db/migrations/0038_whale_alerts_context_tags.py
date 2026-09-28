"""Add moneyness/near_gamma_level/repeat_count to whale_alerts.

Confirmed live, 2026-09-28: comparing a real cluster of Convexa's own SPX
call alerts against a competing platform's for the exact same window
showed 7 of 9 were deep ITM strikes (calls behaving almost like the
underlying itself, weak directional signal) mixed in indistinguishably
with genuine ATM/OTM flow (real leveraged bets, the kind that forces
dealer gamma hedging) -- a raw dollar amount alone doesn't tell them
apart, and the user has to guess at the dashboard instead of the alert
itself saying something useful. These three columns are computed once,
at alert-emission time (WhaleAlertsEngine._emit, backend/domain/use_cases/
flow.py), not derived after the fact:
- moneyness: ITM/ATM/OTM vs. spot at that moment (nullable only for rows
  written before this migration).
- near_gamma_level: which of Call Wall/Put Wall/Gamma Flip (Structural
  view) spot was sitting within band of, if any -- ties the alert to the
  same levels the chart already computes.
- repeat_count: how many alerts this exact contract has produced so far
  this session -- flags real accumulation at one strike versus an
  isolated print.

All three nullable/defaulted -- existing rows are never backfilled (same
low-risk shape as prior migrations this session: old data simply reads
back with the neutral default the storage layer's own read path already
applies, see PostgreSQLStorage.get_recent_whale_alerts's comment), only
new alerts populate them going forward.

Revision ID: 0038_whale_alerts_context_tags
Revises: 0037_index_market_snapshots
Create Date: 2026-09-28
"""

from __future__ import annotations

from alembic import op

revision = "0038_whale_alerts_context_tags"
down_revision = "0037_index_market_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE whale_alerts
        ADD COLUMN IF NOT EXISTS moneyness text,
        ADD COLUMN IF NOT EXISTS near_gamma_level text,
        ADD COLUMN IF NOT EXISTS repeat_count integer
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE whale_alerts
        DROP COLUMN IF EXISTS moneyness,
        DROP COLUMN IF EXISTS near_gamma_level,
        DROP COLUMN IF EXISTS repeat_count
        """
    )
