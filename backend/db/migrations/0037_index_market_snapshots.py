"""Index market_snapshots on (underlying_id, time) -- found live, 2026-09-28,
while verifying migration 0036's fix for option_chain_snapshots: with that
fix deployed, pg_stat_activity during a live scheduler cycle no longer
showed get_latest_chain_snapshot's query at all, but instead showed ~13
concurrent queries against market_snapshots (get_price_history, read by
derived_metrics/VWAP calculation, one call per symbol per cycle) each
taking ~300-500ms. EXPLAIN (ANALYZE, BUFFERS) on the real SPX query
confirmed why: market_snapshots has no index at all (2.7M rows, 178MB),
so every call is a parallel sequential scan of the whole table followed
by a nested loop against `underlyings` that re-scanned that tiny table
133,109 times -- 660ms for one call in isolation, worse under real
15-symbol concurrency.

Same low-risk shape as 0036: CREATE INDEX CONCURRENTLY so this doesn't
lock out market_snapshots' own constant write traffic (save_market_price
runs every cycle, every symbol) while the index builds. underlying_id is
already a direct column here (no denormalization needed, unlike
option_chain_snapshots) -- this migration is just the missing index.

Revision ID: 0037_index_market_snapshots
Revises: 0036_snapshot_underlying_id
Create Date: 2026-09-28
"""

from __future__ import annotations

from alembic import op

revision = "0037_index_market_snapshots"
down_revision = "0036_snapshot_underlying_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS
                idx_market_snapshots_underlying_time
            ON market_snapshots (underlying_id, time)
            """
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS idx_market_snapshots_underlying_time"
        )
