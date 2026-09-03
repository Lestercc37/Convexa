"""Add total_market_gamma, positive_gamma, negative_gamma, peak_gamma_value.

Revision ID: 0020_gamma_aggregate_totals
Revises: 0019_seed_aapl_msft_dia
Create Date: 2026-09-03

Named without "market" (unlike the four column names, which do have
it) purely to fit Alembic's default `alembic_version.version_num`
column, which is `varchar(32)` -- confirmed live, the longer id this
migration originally had failed at the final version-bump step with a
real StringDataRightTruncationError (the DDL itself had already run
inside the same transaction, and rolled back cleanly with it).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0020_gamma_aggregate_totals"
down_revision = "0019_seed_aapl_msft_dia"
branch_labels = None
depends_on = None

_NEW_COLUMNS = (
    "total_market_gamma",
    "positive_gamma",
    "negative_gamma",
    "peak_gamma_value",
)


def upgrade() -> None:
    """Add the four columns, backfilling existing rows to 0 (their
    current, already-wrong-but-consistent value from before this fix --
    see downgrade for why historical rows aren't otherwise touched).
    """
    for column in _NEW_COLUMNS:
        op.add_column(
            "gamma_aggregates",
            sa.Column(column, sa.Numeric(), nullable=False, server_default=sa.text("0")),
        )
    for column in _NEW_COLUMNS:
        op.alter_column("gamma_aggregates", column, server_default=None)


def downgrade() -> None:
    """Remove the four columns."""
    for column in _NEW_COLUMNS:
        op.drop_column("gamma_aggregates", column)
