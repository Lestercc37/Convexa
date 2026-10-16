"""Add delta / vanna / charm exposure per strike to gamma_aggregate_items.

Why: the gamma heatmap (docs/diseno-heatmap-gamma-2026-10-14.txt, sections 9-11) shows delta as a second metric next
to gamma and vanna/charm as last-hour flow context, per strike. The 2026-10-15 study (60 sessions) found the day
extreme lands near the strike of largest |net delta| as often as near the largest |gamma| strike, so delta is kept
per strike from now on. The columns are nullable: rows written before this migration stay NULL ("not stored"),
never a false 0.

Units, as stored by FakeGammaExposureCalculator (documented there too): dollar notional = greek x OI x 100 x spot.
delta_exposure: ThetaData's own delta, calls positive / puts negative (no dealer sign), summed at the strike.
vanna_exposure, charm_exposure: BSM from ThetaData's IV with the dealer sign of the GEX (call +1, put -1);
vanna per 1.00 of IV (divide by 100 for one IV point), charm per year (divide by 8,760 for one hour).

Revision ID: 0042_gamma_item_greeks
Revises: 0041_whale_alerts_conditions
Create Date: 2026-10-15
"""

from __future__ import annotations

from alembic import op

revision = "0042_gamma_item_greeks"
down_revision = "0041_whale_alerts_conditions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE gamma_aggregate_items ADD COLUMN IF NOT EXISTS delta_exposure numeric")
    op.execute("ALTER TABLE gamma_aggregate_items ADD COLUMN IF NOT EXISTS vanna_exposure numeric")
    op.execute("ALTER TABLE gamma_aggregate_items ADD COLUMN IF NOT EXISTS charm_exposure numeric")


def downgrade() -> None:
    op.execute("ALTER TABLE gamma_aggregate_items DROP COLUMN IF EXISTS charm_exposure")
    op.execute("ALTER TABLE gamma_aggregate_items DROP COLUMN IF EXISTS vanna_exposure")
    op.execute("ALTER TABLE gamma_aggregate_items DROP COLUMN IF EXISTS delta_exposure")
