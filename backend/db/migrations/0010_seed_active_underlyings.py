"""Seed the active underlyings.

Revision ID: 0010_seed_active_underlyings
Revises: 0009_absolute_gamma_strike
Create Date: 2026-08-06
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

from backend.domain.underlyings import ACTIVE_UNDERLYINGS

revision = "0010_seed_active_underlyings"
down_revision = "0009_absolute_gamma_strike"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Insert active symbols and correct any stale classifications."""
    connection = op.get_bind()
    statement = text(
        """
        INSERT INTO underlyings (symbol, kind, is_priority)
        VALUES (:symbol, :kind, :is_priority)
        ON CONFLICT (symbol) DO UPDATE SET
            kind = EXCLUDED.kind,
            is_priority = EXCLUDED.is_priority
        """
    )
    connection.execute(
        statement,
        [
            {
                "symbol": underlying.symbol,
                "kind": underlying.kind.value,
                "is_priority": underlying.is_priority,
            }
            for underlying in ACTIVE_UNDERLYINGS
        ],
    )


def downgrade() -> None:
    """Keep reference rows because they may predate this migration or be in use."""
