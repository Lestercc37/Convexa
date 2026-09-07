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
    """Insert active symbols and correct any stale classifications.

    Filtered to kind in ('equity', 'index') -- the only two values
    underlyings_kind_check (migration 0001) allows at this point in the
    chain. ACTIVE_UNDERLYINGS is imported live here, not a frozen
    snapshot, so replaying migrations from scratch against today's
    constant (which now also carries 'future', added later by migration
    0015) violates that constraint before 0015 ever widens it --
    confirmed live, 2026-09, bootstrapping a fresh test database from
    revision <base>. 0015 seeds the 'future' rows itself, immediately
    after widening the constraint that allows them.
    """
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
            if underlying.kind.value in ("equity", "index")
        ],
    )


def downgrade() -> None:
    """Keep reference rows because they may predate this migration or be in use."""
