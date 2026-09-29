"""Add near_the_money_width to gamma_aggregates.

Confirmed live, 2026-09-29: a real SPXW put alert (strike 7655, spot ~7682,
$27/0.35% away) was tagged ATM by WhaleAlertsEngine._classify_moneyness's
flat ATM_BAND_PCT (1% of spot) -- fine for a $230 stock (1% = ~$2.30, about
one strike) but ~15 SPX strikes wide, since SPX's own weekly strikes are $5
apart and its spot sits in the thousands. CalculateGammaExposureOrchestrator
already computes an ATR-based, per-symbol-scale-aware near-the-money width
every cycle (see calculate_near_the_money_width.py) to select Call Wall/Put
Wall/Net GEX's own narrow strike window -- this column exposes that
already-computed value so WhaleAlertsEngine can classify moneyness/near-
gamma-level against it instead of a hand-tuned percentage, with no new
per-trade computation or provider call.

Nullable, not backfilled -- old rows read back with the storage layer's
existing flat-percentage fallback (same low-risk shape as 0038's three
columns) until the next scheduler cycle writes a fresh aggregate.

Revision ID: 0039_gamma_aggregates_near_the_money_width
Revises: 0038_whale_alerts_context_tags
Create Date: 2026-09-29
"""

from __future__ import annotations

from alembic import op

revision = "0039_gamma_aggregates_near_the_money_width"
down_revision = "0038_whale_alerts_context_tags"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE gamma_aggregates
        ADD COLUMN IF NOT EXISTS near_the_money_width numeric
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE gamma_aggregates
        DROP COLUMN IF EXISTS near_the_money_width
        """
    )
