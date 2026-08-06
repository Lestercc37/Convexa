"""Create and seed per-symbol whale thresholds.

Revision ID: 0012_whale_thresholds
Revises: 0011_vanna_exposure
Create Date: 2026-08-06
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

from backend.domain.underlyings import ACTIVE_UNDERLYINGS

revision = "0012_whale_thresholds"
down_revision = "0011_vanna_exposure"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the table and seed every active underlying with IWM defaults."""
    op.execute(
        """
        CREATE TABLE whale_thresholds (
            underlying_id integer PRIMARY KEY REFERENCES underlyings(id),
            unusual_min numeric NOT NULL,
            whale_min numeric NOT NULL,
            unusual_multiplier numeric NOT NULL,
            whale_multiplier numeric NOT NULL
        )
        """
    )
    connection = op.get_bind()
    connection.execute(
        text(
            """
            INSERT INTO whale_thresholds (
                underlying_id, unusual_min, whale_min,
                unusual_multiplier, whale_multiplier
            )
            SELECT id, :unusual_min, :whale_min,
                   :unusual_multiplier, :whale_multiplier
            FROM underlyings
            WHERE symbol = :symbol
            ON CONFLICT (underlying_id) DO UPDATE SET
                unusual_min = EXCLUDED.unusual_min,
                whale_min = EXCLUDED.whale_min,
                unusual_multiplier = EXCLUDED.unusual_multiplier,
                whale_multiplier = EXCLUDED.whale_multiplier
            """
        ),
        [
            {
                "symbol": underlying.symbol,
                "unusual_min": 40000,
                "whale_min": 150000,
                "unusual_multiplier": 3.0,
                "whale_multiplier": 6.0,
            }
            for underlying in ACTIVE_UNDERLYINGS
        ],
    )


def downgrade() -> None:
    """Drop per-symbol whale threshold configuration."""
    op.execute("DROP TABLE whale_thresholds")
