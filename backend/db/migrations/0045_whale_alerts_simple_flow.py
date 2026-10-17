"""Add whale_alerts.simple_flow: the alert's buy/sell premium counting only trades with a trusted-side condition code.

Why (2026-10-16, heatmap design 12.12-12.14): the engine signs every trade buy/sell by price against the bid/ask, whatever its
OPRA condition. Multi-leg trades (codes 130-144, ~42% of the day's SPXW premium on 2026-10-16) and floor trades (129) cannot
be signed that way reliably, so an alert's COMPRA/VENTA label can rest on trades whose side is not meaningful. Lester chose a
TRIAL with two labels (option C): the main label stays as is, and this column records a second buy/sell split that counts only
conditions 18 (auto execution), 95 (intermarket sweep) and 125/126 (single-leg auction), as JSON {"buy": <USD>, "sell": <USD>}.
Whatever premium is in neither number (amount - buy - sell) is the "no side" part.

TRIAL / CAPTURE ONLY: no query reads this column and no alert, net-pressure figure or volume is computed differently because
of it (PostgreSQLStorage.get_recent_whale_alerts keeps its explicit column list). Comparing the two labels over several
sessions is a separate, later step.

Nullable, no default, no backfill: a metadata-only change for Postgres; existing rows (and BVC-derived ones) stay NULL.
Same shape as 0041.

Revision ID: 0045_whale_alerts_simple_flow
Revises: 0044_contract_volume_corrections
Create Date: 2026-10-16
"""

from __future__ import annotations

from alembic import op

revision = "0045_whale_alerts_simple_flow"
down_revision = "0044_contract_volume_corrections"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Brief ACCESS EXCLUSIVE lock for the catalog change; give up after 5 s instead of queueing every insert/select behind a
    # long transaction (SET LOCAL: only this migration's transaction). A failed run changes nothing and can be repeated.
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("ALTER TABLE whale_alerts ADD COLUMN IF NOT EXISTS simple_flow jsonb")


def downgrade() -> None:
    op.execute("ALTER TABLE whale_alerts DROP COLUMN IF EXISTS simple_flow")
