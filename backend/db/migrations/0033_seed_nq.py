"""Seed NQ (E-mini Nasdaq-100 future) into underlyings and whale_thresholds.

Revision ID: 0033_seed_nq
Revises: 0032_create_invites_table
Create Date: 2026-09-27
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

from backend.domain.underlyings import ACTIVE_UNDERLYINGS

revision = "0033_seed_nq"
down_revision = "0032_create_invites_table"
branch_labels = None
depends_on = None

# Same defaults 0012/0016/0018 seeded every other active symbol with.
_DEFAULT_UNUSUAL_MIN = 40000
_DEFAULT_WHALE_MIN = 150000
_DEFAULT_UNUSUAL_MULTIPLIER = 3.0
_DEFAULT_WHALE_MULTIPLIER = 6.0
_DEFAULT_SUSTAINED_FLOW_MIN = 500000


def upgrade() -> None:
    """Re-run the 0010/0015/0018 underlyings seed, then add NQ's whale_thresholds row.

    Same pattern as 0018 (NDX): NQ's kind ("future") is already a valid
    underlyings.kind value since 0015 widened the CHECK constraint for
    ES, so no constraint change is needed here, only the seed itself.
    """
    connection = op.get_bind()
    connection.execute(
        text(
            """
            INSERT INTO underlyings (symbol, kind, is_priority)
            VALUES (:symbol, :kind, :is_priority)
            ON CONFLICT (symbol) DO UPDATE SET
                kind = EXCLUDED.kind,
                is_priority = EXCLUDED.is_priority
            """
        ),
        [
            {
                "symbol": underlying.symbol,
                "kind": underlying.kind.value,
                "is_priority": underlying.is_priority,
            }
            for underlying in ACTIVE_UNDERLYINGS
        ],
    )
    connection.execute(
        text(
            """
            INSERT INTO whale_thresholds (
                underlying_id, unusual_min, whale_min,
                unusual_multiplier, whale_multiplier, sustained_flow_min
            )
            SELECT id, :unusual_min, :whale_min,
                   :unusual_multiplier, :whale_multiplier, :sustained_flow_min
            FROM underlyings
            WHERE symbol = 'NQ'
            ON CONFLICT (underlying_id) DO NOTHING
            """
        ),
        {
            "unusual_min": _DEFAULT_UNUSUAL_MIN,
            "whale_min": _DEFAULT_WHALE_MIN,
            "unusual_multiplier": _DEFAULT_UNUSUAL_MULTIPLIER,
            "whale_multiplier": _DEFAULT_WHALE_MULTIPLIER,
            "sustained_flow_min": _DEFAULT_SUSTAINED_FLOW_MIN,
        },
    )


def downgrade() -> None:
    """Remove NQ's rows only -- every other symbol predates this migration."""
    op.execute(
        """
        DELETE FROM whale_thresholds
        WHERE underlying_id = (SELECT id FROM underlyings WHERE symbol = 'NQ')
        """
    )
    op.execute("DELETE FROM underlyings WHERE symbol = 'NQ'")
