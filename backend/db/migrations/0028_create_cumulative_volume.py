"""Create contract_cumulative_volume -- lets a process without its own
live ThetaData trade stream (the new scheduler-only process, splitting
UnderlyingRefreshScheduler out of backend/worker.py to stop it
contending with ThetaStreamHub's own event loop for the GIL) still
report each contract's real cumulative volume in the chains it fetches
and persists, instead of silently reporting 0 for everything.

Revision ID: 0028_cumulative_volume
Revises: 0027_delta_exposure
Create Date: 2026-09-22
"""

from __future__ import annotations

from alembic import op

revision = "0028_cumulative_volume"
down_revision = "0027_delta_exposure"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Keyed directly on occ_symbol (already the unique natural key
    # ThetaStreamHub's own in-memory _cumulative_volume dict uses, and
    # already UNIQUE on option_contracts) -- no need for a contract_id
    # FK/join just to look this up. Not a time-series table (like
    # theta_request_slots, not like the hypertables): one row per
    # contract, overwritten in place, not appended.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS contract_cumulative_volume (
            occ_symbol text PRIMARY KEY,
            volume bigint NOT NULL,
            updated_at timestamptz NOT NULL
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS contract_cumulative_volume")
