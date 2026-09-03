"""Allow gamma_aggregates.gamma_flip to be NULL.

Revision ID: 0021_gamma_flip_nullable
Revises: 0020_gamma_aggregate_totals
Create Date: 2026-09-03
"""

from __future__ import annotations

from alembic import op

revision = "0021_gamma_flip_nullable"
down_revision = "0020_gamma_aggregate_totals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Drop NOT NULL -- NULL now means "no sign crossing found in the
    scanned strike range", a real, distinct outcome from a flip found
    at strike 0 that the previous NOT NULL constraint (with the domain
    layer's own Decimal("0") default) made impossible to represent.
    Existing rows keep whatever value they already have -- their 0s
    predate this fix and aren't retroactively reinterpreted.
    """
    op.alter_column("gamma_aggregates", "gamma_flip", nullable=True)


def downgrade() -> None:
    """Restore NOT NULL -- any NULL rows written under this migration
    would need backfilling to 0 first, same as any other NOT NULL
    restoration; not done automatically here since a real NULL and a
    real 0 mean different things and silently conflating them on
    downgrade would be its own data-loss bug.
    """
    op.alter_column("gamma_aggregates", "gamma_flip", nullable=False)
