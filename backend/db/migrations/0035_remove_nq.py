"""Remove NQ -- confirmed live, 2026-09-28 (real market open): ThetaData's
Options subscription does not cover futures options at all (confirmed
directly with the user, who already knew this from experience with the
vendor). NQ's option-chain fetch fails outright (500 "Expected exactly
one quote; got 0"), unlike ES which returns a chain but -- now suspected,
not yet root-caused -- likely isn't real ES futures options data either
(its own Gamma/Walls have been sitting at an implausibly small, collapsed
scale since 2026-09-02, long before this session ever touched ES/NQ).
NQ never worked even once in production; removed rather than left as a
permanently broken symbol the scheduler retries every cycle, wasting one
of the account's 8 concurrent ThetaData request slots on every failure
during the exact morning that budget is under the most real contention.

Revision ID: 0035_remove_nq
Revises: 0034_create_future_price_anchors
Create Date: 2026-09-28
"""

from __future__ import annotations

from alembic import op

revision = "0035_remove_nq"
down_revision = "0034_create_future_price_anchors"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DELETE FROM whale_thresholds
        WHERE underlying_id = (SELECT id FROM underlyings WHERE symbol = 'NQ')
        """
    )
    op.execute(
        """
        DELETE FROM gamma_aggregate_items
        WHERE underlying_id = (SELECT id FROM underlyings WHERE symbol = 'NQ')
        """
    )
    op.execute(
        """
        DELETE FROM gamma_aggregates
        WHERE underlying_id = (SELECT id FROM underlyings WHERE symbol = 'NQ')
        """
    )
    op.execute(
        """
        DELETE FROM future_price_anchors
        WHERE underlying_id = (SELECT id FROM underlyings WHERE symbol = 'NQ')
        """
    )
    op.execute("DELETE FROM underlyings WHERE symbol = 'NQ'")


def downgrade() -> None:
    """Re-seed NQ -- same upsert 0033_seed_nq itself used."""
    from backend.domain.underlyings import ACTIVE_UNDERLYINGS

    connection = op.get_bind()
    from sqlalchemy import text

    nq = next((u for u in ACTIVE_UNDERLYINGS if u.symbol == "NQ"), None)
    if nq is None:
        # ACTIVE_UNDERLYINGS no longer lists NQ at all (this downgrade()
        # only matters for a real rollback, not the normal forward path)
        # -- seed it directly with the same values 0033 used.
        connection.execute(
            text(
                """
                INSERT INTO underlyings (symbol, kind, is_priority)
                VALUES ('NQ', 'future', true)
                ON CONFLICT (symbol) DO NOTHING
                """
            )
        )
    else:
        connection.execute(
            text(
                """
                INSERT INTO underlyings (symbol, kind, is_priority)
                VALUES (:symbol, :kind, :is_priority)
                ON CONFLICT (symbol) DO NOTHING
                """
            ),
            {"symbol": nq.symbol, "kind": nq.kind.value, "is_priority": nq.is_priority},
        )
