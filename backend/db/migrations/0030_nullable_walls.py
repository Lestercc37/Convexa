"""Make gamma_aggregates.call_wall/put_wall nullable.

NULL now means "no valid wall candidate found on that side" (e.g. every
in-range strike nets the same sign, so there's nothing to call a put
wall) -- a real, distinct outcome from "the wall is at strike 0", which
the old NOT NULL columns silently conflated (the orchestrator's own
fallback, when CalculateWallsUseCase found nothing, was the dataclass's
unset Decimal("0") default -- never a real computed value). Same pattern
gamma_flip already established (0021_gamma_flip_nullable).

Found live, 2026-09-25: SPX's own Tactical (0-2 DTE) window is narrow
enough to hit this for real -- put_wall came back a fake $0, and the
price chart's own autoscale stretched down to include it, visually
collapsing every other level into a sliver at the top of the chart.

Revision ID: 0030_nullable_walls
Revises: 0029_gamma_aggregate_view
Create Date: 2026-09-25
"""

from __future__ import annotations

from alembic import op

revision = "0030_nullable_walls"
down_revision = "0029_gamma_aggregate_view"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("gamma_aggregates", "call_wall", nullable=True)
    op.alter_column("gamma_aggregates", "put_wall", nullable=True)


def downgrade() -> None:
    """Destructive if any row actually has a NULL wall by then -- those
    rows would need a real value backfilled (0, matching the old silent
    default) before the column can go back to NOT NULL."""
    op.execute("UPDATE gamma_aggregates SET call_wall = 0 WHERE call_wall IS NULL")
    op.execute("UPDATE gamma_aggregates SET put_wall = 0 WHERE put_wall IS NULL")
    op.alter_column("gamma_aggregates", "call_wall", nullable=False)
    op.alter_column("gamma_aggregates", "put_wall", nullable=False)
