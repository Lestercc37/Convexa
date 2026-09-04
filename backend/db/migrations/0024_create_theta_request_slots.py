"""Create theta_request_slots -- a Postgres-backed distributed semaphore
for ThetaData's real account-wide concurrent-request limit (8, per
backend/adapters/providers/thetadata/provider.py's own
THETADATA_MAX_CONCURRENT_REQUESTS).

Revision ID: 0024_theta_slots
Revises: 0023_whale_alerts
Create Date: 2026-09-04
"""

from __future__ import annotations

from alembic import op

revision = "0024_theta_slots"
down_revision = "0023_whale_alerts"
branch_labels = None
depends_on = None

# Kept in sync by hand with THETADATA_MAX_CONCURRENT_REQUESTS -- the
# real value confirmed live from the running Theta Terminal's own log
# (see that constant's own comment for the citation).
SLOT_COUNT = 8


def upgrade() -> None:
    """8 pre-seeded rows, one per concurrent-request slot -- not a
    time-series table, so no hypertable conversion (unlike every other
    table in this schema). `acquired_at IS NULL` means free; a slot
    whose `acquired_at` is older than 30 seconds is also treated as
    free by the acquiring query, recovering a slot a crashed process
    never released (see AsyncPostgreSQLStorage.acquire_theta_slot /
    PostgreSQLStorage.acquire_theta_slot)."""
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS theta_request_slots (
            slot integer PRIMARY KEY,
            acquired_at timestamptz NULL,
            holder text NULL
        )
        """
    )
    op.execute(
        f"""
        INSERT INTO theta_request_slots (slot)
        SELECT generate_series(1, {SLOT_COUNT})
        ON CONFLICT (slot) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS theta_request_slots")
