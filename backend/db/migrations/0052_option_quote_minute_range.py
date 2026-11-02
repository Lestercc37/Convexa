"""option_quote_minutes gets the range inside each minute: highest/lowest mid of every quote and highest/lowest single-leg trade price.

Why (Lester 2026-11-02): the Call | Put contract chart drew SPXW 11-02 P7620's peak at +$4,420 (mid 63.20, the last quote of 14:48)
while the contract traded up to 64.81 on thinkorswim (+$4,581): only the LAST bid/ask of each minute was kept, so a spike that came
back inside the minute was lost. Nullable, no default: older rows keep NULL (= only the last quote is known), and adding the columns
is a catalog change only (no table rewrite).

Revision ID: 0052_option_quote_minute_range
Revises: 0051_invite_purpose
Create Date: 2026-11-02
"""

from __future__ import annotations

from alembic import op

revision = "0052_option_quote_minute_range"
down_revision = "0051_invite_purpose"
branch_labels = None
depends_on = None

COLUMNS = ("mid_high", "mid_low", "trade_high", "trade_low")


def upgrade() -> None:
    for column in COLUMNS:
        op.execute(f"ALTER TABLE option_quote_minutes ADD COLUMN IF NOT EXISTS {column} numeric(14, 4)")


def downgrade() -> None:
    for column in COLUMNS:
        op.execute(f"ALTER TABLE option_quote_minutes DROP COLUMN IF EXISTS {column}")
