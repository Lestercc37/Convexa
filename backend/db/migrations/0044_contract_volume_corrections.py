"""Create contract_volume_corrections -- the option volume ThetaData reports per contract at the last correction pass of the trade date, kept
APART from the live counter in contract_cumulative_volume.

Why: a contract that enters the stream subscription band late (the band follows the price, additively, rediscovered every 60 s) only counts what traded
after it was subscribed. On 2026-10-16 (SPX 7750 -> 7660) the SPXW volume was 4.71% short over the contracts ThetaData returned (strikes <= 7600: -27%).
The live counter must stay continuous (anything that computes CHANGES of volume, e.g. the snapshot-delta whale detector or a future time-bucketed volume
layer, would read a jump as traded volume), so the correction is stored on its own and only the readers of TOTALS (the /chain reply) take
max(counter, correction).

Additive: a new table, nothing existing is touched. Written only by the correction pass (python -m backend.volume_correction_pass).

Revision ID: 0044_contract_volume_corrections
Revises: 0043_large_trade_prints
Create Date: 2026-10-16
"""

from __future__ import annotations

from alembic import op

revision = "0044_contract_volume_corrections"
down_revision = "0043_large_trade_prints"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Keyed on occ_symbol like contract_cumulative_volume. as_of = when the pass read ThetaData; a row older than the current TRADE DATE
    # (it opens 20:15 ET, see use_cases/trade_date.py) is a previous session's figure and is ignored/replaced.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS contract_volume_corrections (
            occ_symbol text PRIMARY KEY,
            volume bigint NOT NULL,
            as_of timestamptz NOT NULL
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS contract_volume_corrections")
