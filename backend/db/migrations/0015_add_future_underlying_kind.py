"""Allow 'future' as a third underlyings.kind value, and seed ES.

Revision ID: 0015_add_future_underlying_kind
Revises: 0014_gamma_aggregate_items
Create Date: 2026-08-10
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

from backend.domain.underlyings import ACTIVE_UNDERLYINGS

revision = "0015_add_future_underlying_kind"
down_revision = "0014_gamma_aggregate_items"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Widen the kind CHECK constraint, then re-run the 0010 seed.

    ES (E-mini S&P 500 future) was missing from the original PR #44 seed —
    `ACTIVE_UNDERLYINGS` now includes it with kind="future", a value the
    original `underlyings_kind_check` (migration 0001) never allowed.
    Re-running the same idempotent upsert as 0010 against the current
    `ACTIVE_UNDERLYINGS` both adds the ES row and leaves the other 10
    untouched (ON CONFLICT DO UPDATE with identical values).
    """
    op.execute(
        """
        ALTER TABLE underlyings DROP CONSTRAINT underlyings_kind_check
        """
    )
    op.execute(
        """
        ALTER TABLE underlyings
        ADD CONSTRAINT underlyings_kind_check CHECK (kind IN ('equity', 'index', 'future'))
        """
    )
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


def downgrade() -> None:
    """Remove the ES row (net-new here, unlike 0010's pre-existing 10) and
    restore the original two-value constraint — re-adding a stricter CHECK
    while a 'future' row still exists would fail Postgres's validation of
    existing rows.
    """
    op.execute("DELETE FROM underlyings WHERE kind = 'future'")
    op.execute(
        """
        ALTER TABLE underlyings DROP CONSTRAINT underlyings_kind_check
        """
    )
    op.execute(
        """
        ALTER TABLE underlyings
        ADD CONSTRAINT underlyings_kind_check CHECK (kind IN ('equity', 'index'))
        """
    )
