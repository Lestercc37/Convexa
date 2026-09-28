"""Add a nullable underlying_id to option_chain_snapshots and index it,
so get_latest_chain_snapshot's "give me the latest chain for this symbol"
query can go straight there instead of looping through every historical
option_contracts row for the symbol (11,698 for SPX alone) via a nested
loop over the existing (contract_id, time DESC) index -- confirmed live,
2026-09-28: even after scoping that query to a recent time window (see
that query's own comment, and the query-shape fix this follows), 15
symbols computing gamma concurrently every scheduler cycle meant ~10
Postgres backends genuinely CPU-bound at once (not blocked on I/O or
locks -- checked pg_stat_activity directly), because each one still had
to probe the index once per historical contract just to ask "do you have
anything recent?". A direct (underlying_id, time DESC) index answers that
with one range scan instead.

Deliberately does NOT backfill underlying_id for the table's existing
~41.7M rows -- an UPDATE at that scale would itself be a slow, lock-heavy
operation on a table 15 symbols write to every ~30s. Every NEW row gets
underlying_id from save_chain_snapshot going forward (this migration's
sibling code change); the read path's new fast tier simply falls through
to the existing recent-window query (still correct, just slower) when it
finds nothing, which is expected only until each symbol's first write
after this deploys.

CREATE INDEX CONCURRENTLY, not a plain CREATE INDEX -- avoids taking the
lock a normal index build would hold against this table's real write
traffic for however long building an index over 41.7M rows takes.
Requires running outside this migration's own transaction (Postgres
forbids CONCURRENTLY inside one), hence op.get_context().autocommit_block().

Revision ID: 0036_snapshot_underlying_id
Revises: 0035_remove_nq
Create Date: 2026-09-28
"""

from __future__ import annotations

from alembic import op

revision = "0036_snapshot_underlying_id"
down_revision = "0035_remove_nq"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE option_chain_snapshots
        ADD COLUMN IF NOT EXISTS underlying_id integer REFERENCES underlyings(id)
        """
    )
    with op.get_context().autocommit_block():
        op.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS
                idx_option_chain_snapshots_underlying_time
            ON option_chain_snapshots (underlying_id, time DESC)
            """
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS idx_option_chain_snapshots_underlying_time"
        )
    op.execute("ALTER TABLE option_chain_snapshots DROP COLUMN IF EXISTS underlying_id")
