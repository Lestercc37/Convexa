"""Nightly retention for gamma_fast_items (the 10-second "fast gamma layer", migration 0047): delete rows older than N days.

Why: the layer writes ~550,000 rows (~160 MB with indexes) per session. Nothing reads rows older than a few sessions (the heatmap asks for the current session; the replay idea
would need more and is not built), and the migration's own note says "purge old rows with DELETE ... interval '7 days'". Lester approved automating that on 2026-10-22.

Safety:
  * the cutoff is the START of an Eastern-time day (today - N days at 00:00 ET), so a session is never cut in half;
  * deletes in small batches, each in its own transaction (short locks, the table stays readable and writable for the fast layer), with a pause between batches and a time budget;
  * refuses to run during market hours (09:30-16:15 ET, Mon-Fri) unless --force, and refuses N < 3;
  * --dry-run only counts what would be deleted per day;
  * prints counts only.

Usage (scheduled task ConvexaFastGammaRetention, daily 17:00 ET):  python -m backend.scripts.fast_gamma_retention [--days 7] [--batch-rows 20000] [--max-seconds 900] [--dry-run] [--force]
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from datetime import time as dtime
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")
MIN_DAYS = 3
MARKET_OPEN = dtime(9, 30)
MARKET_BLOCK_UNTIL = dtime(16, 15)


def cutoff_for(now: datetime, days: int) -> datetime:
    """00:00 Eastern of (today - days), as an aware datetime. Rows with time < cutoff are deleted."""
    local = now.astimezone(EASTERN)
    midnight = datetime(local.year, local.month, local.day, tzinfo=EASTERN)
    return (midnight - timedelta(days=days)).astimezone(UTC)


def in_market_hours(now: datetime) -> bool:
    local = now.astimezone(EASTERN)
    return local.weekday() < 5 and MARKET_OPEN <= local.time() < MARKET_BLOCK_UNTIL


def purge(
    delete_batch: Callable[[datetime, int], int],
    cutoff: datetime,
    batch_rows: int,
    max_seconds: float,
    pause_seconds: float = 0.2,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[int, bool]:
    """Run delete_batch(cutoff, batch_rows) until it deletes nothing or the time budget is spent. Returns (rows deleted, finished)."""
    started = clock()
    total = 0
    while True:
        if clock() - started >= max_seconds:
            return total, False
        deleted = delete_batch(cutoff, batch_rows)
        total += deleted
        if deleted == 0:
            return total, True
        sleep(pause_seconds)


def _engine():
    from backend.core.settings import get_settings
    from backend.infrastructure.database.engine import create_sync_engine

    return create_sync_engine(get_settings().database_url)


def count_by_day(engine, cutoff: datetime) -> list[tuple[str, int]]:
    from sqlalchemy import text

    with engine.connect() as connection:
        connection.execute(text("SET statement_timeout = '120s'"))
        rows = connection.execute(
            text(
                "SELECT (time AT TIME ZONE 'America/New_York')::date AS day, count(*) FROM gamma_fast_items "
                "WHERE time < :cutoff GROUP BY 1 ORDER BY 1"
            ),
            {"cutoff": cutoff},
        ).all()
    return [(str(day), int(n)) for day, n in rows]


def make_delete_batch(engine) -> Callable[[datetime, int], int]:
    from sqlalchemy import text

    def delete_batch(cutoff: datetime, batch_rows: int) -> int:
        with engine.begin() as connection:
            connection.execute(text("SET LOCAL lock_timeout = '5s'"))
            connection.execute(text("SET LOCAL statement_timeout = '60s'"))
            result = connection.execute(
                text(
                    "DELETE FROM gamma_fast_items WHERE ctid IN "
                    "(SELECT ctid FROM gamma_fast_items WHERE time < :cutoff LIMIT :n)"
                ),
                {"cutoff": cutoff, "n": batch_rows},
            )
            return int(result.rowcount or 0)

    return delete_batch


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--batch-rows", type=int, default=20_000)
    parser.add_argument("--max-seconds", type=float, default=900.0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="allow running during market hours")
    args = parser.parse_args(argv)
    now = datetime.now(UTC)
    if args.days < MIN_DAYS:
        print(f"refusing: --days {args.days} is below the minimum of {MIN_DAYS}")
        return 2
    if in_market_hours(now) and not args.force:
        print("refusing: market hours (09:30-16:15 ET); run after the close or pass --force")
        return 3
    cutoff = cutoff_for(now, args.days)
    stamp = now.astimezone(EASTERN).strftime("%Y-%m-%d %H:%M:%S ET")
    engine = _engine()
    try:
        pending = count_by_day(engine, cutoff)
        total_pending = sum(n for _, n in pending)
        print(f"{stamp} gamma_fast_items retention: keep {args.days} days + today, cutoff {cutoff.astimezone(EASTERN):%Y-%m-%d %H:%M} ET; rows to delete {total_pending:,} in {len(pending)} day(s): {pending}")
        if args.dry_run or total_pending == 0:
            print("dry run, nothing deleted" if args.dry_run else "nothing to delete")
            return 0
        deleted, finished = purge(make_delete_batch(engine), cutoff, args.batch_rows, args.max_seconds)
        remaining = sum(n for _, n in count_by_day(engine, cutoff))
        print(f"deleted {deleted:,} rows; remaining older than the cutoff {remaining:,}; {'finished' if finished else 'stopped at the time budget, the next run continues'}")
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
