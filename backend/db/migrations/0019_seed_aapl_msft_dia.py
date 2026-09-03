"""Seed AAPL, MSFT, and DIA into underlyings and whale_thresholds.

Revision ID: 0019_seed_aapl_msft_dia
Revises: 0018_seed_ndx
Create Date: 2026-09-03
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

from backend.domain.underlyings import ACTIVE_UNDERLYINGS

revision = "0019_seed_aapl_msft_dia"
down_revision = "0018_seed_ndx"
branch_labels = None
depends_on = None

_NEW_SYMBOLS = ("AAPL", "MSFT", "DIA")

# Same defaults 0012/0016/0018 seeded every other active symbol with.
_DEFAULT_UNUSUAL_MIN = 40000
_DEFAULT_WHALE_MIN = 150000
_DEFAULT_UNUSUAL_MULTIPLIER = 3.0
_DEFAULT_WHALE_MULTIPLIER = 6.0
_DEFAULT_SUSTAINED_FLOW_MIN = 500000


def upgrade() -> None:
    """Re-run the 0010/0015/0018 underlyings seed, then add AAPL/MSFT/DIA's
    whale_thresholds rows. Same reasoning as 0018 -- see that migration's
    own docstring for the full explanation of both halves.

    `underlyings`: re-running the full idempotent upsert against the
    current `ACTIVE_UNDERLYINGS` adds the 3 new rows and leaves the other
    12 untouched (ON CONFLICT DO UPDATE with identical values) -- safe,
    `kind`/`is_priority` aren't customizable through any API.

    `whale_thresholds`: deliberately scoped to only insert rows for these
    3 new symbols (ON CONFLICT DO NOTHING), never touching an existing
    row -- `PATCH /whale-thresholds/{symbol}` lets thresholds be
    customized per symbol at runtime, so re-asserting defaults across
    every symbol here would silently clobber any real customization.
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
            WHERE symbol = ANY(:symbols)
            ON CONFLICT (underlying_id) DO NOTHING
            """
        ),
        {
            "unusual_min": _DEFAULT_UNUSUAL_MIN,
            "whale_min": _DEFAULT_WHALE_MIN,
            "unusual_multiplier": _DEFAULT_UNUSUAL_MULTIPLIER,
            "whale_multiplier": _DEFAULT_WHALE_MULTIPLIER,
            "sustained_flow_min": _DEFAULT_SUSTAINED_FLOW_MIN,
            "symbols": list(_NEW_SYMBOLS),
        },
    )


def downgrade() -> None:
    """Remove AAPL/MSFT/DIA's rows only -- every other symbol predates this migration."""
    op.execute(
        """
        DELETE FROM whale_thresholds
        WHERE underlying_id IN (
            SELECT id FROM underlyings WHERE symbol IN ('AAPL', 'MSFT', 'DIA')
        )
        """
    )
    op.execute("DELETE FROM underlyings WHERE symbol IN ('AAPL', 'MSFT', 'DIA')")
