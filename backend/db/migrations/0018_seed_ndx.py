"""Seed NDX (Nasdaq-100 index) into underlyings and whale_thresholds.

Revision ID: 0018_seed_ndx
Revises: 0017_screener_preset_settings
Create Date: 2026-09-03
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

from backend.domain.underlyings import ACTIVE_UNDERLYINGS

revision = "0018_seed_ndx"
down_revision = "0017_screener_preset_settings"
branch_labels = None
depends_on = None

# Same defaults 0012/0016 seeded every other active symbol with.
_DEFAULT_UNUSUAL_MIN = 40000
_DEFAULT_WHALE_MIN = 150000
_DEFAULT_UNUSUAL_MULTIPLIER = 3.0
_DEFAULT_WHALE_MULTIPLIER = 6.0
_DEFAULT_SUSTAINED_FLOW_MIN = 500000


def upgrade() -> None:
    """Re-run the 0010/0015 underlyings seed, then add NDX's whale_thresholds row.

    NDX was confirmed working when subscribed directly to ThetaData, but was
    never added to `ACTIVE_UNDERLYINGS` — same class of gap 0015 fixed for ES
    ("missing from the original seed"), except NDX's kind ("index") was
    already a valid `underlyings.kind` value, so no CHECK constraint change
    is needed here, only the seed itself.

    `underlyings`: re-running the same idempotent upsert as 0010/0015 against
    the current `ACTIVE_UNDERLYINGS` adds the NDX row and leaves the other 12
    untouched (ON CONFLICT DO UPDATE with identical values) -- safe because
    `kind`/`is_priority` aren't customizable through any API.

    `whale_thresholds`, unlike `underlyings`, deliberately does NOT re-run
    0012's blanket upsert: `PATCH /whale-thresholds/{symbol}`
    (backend/api/routes/whale_thresholds.py) lets thresholds be customized
    per symbol at runtime, so re-asserting 0012's defaults across every
    symbol here would silently clobber any real customization made since
    then. Only NDX gets a row inserted (ON CONFLICT DO NOTHING), identical
    to what 0012/0016 originally seeded every other symbol with -- every
    other symbol's row is untouched.
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
            WHERE symbol = 'NDX'
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
    """Remove NDX's rows only -- every other symbol predates this migration."""
    op.execute(
        """
        DELETE FROM whale_thresholds
        WHERE underlying_id = (SELECT id FROM underlyings WHERE symbol = 'NDX')
        """
    )
    op.execute("DELETE FROM underlyings WHERE symbol = 'NDX'")
