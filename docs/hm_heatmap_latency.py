"""READ-ONLY: latency and size of the heatmap endpoint's SQL (AsyncPostgreSQLStorage.get_gamma_heatmap) on the real gamma_aggregate_items,
for 1 day and 5 days of SPX/NDX/SPY tactical at 120 s buckets. Runs the same statement through a SYNC read-only session (identical plan),
5 repetitions each; prints rows, strikes, columns, response size estimate and min/median/max milliseconds. Changes nothing."""
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "V:/Convexa/repo")
from sqlalchemy import text
from backend.core.container import build_container

SQL = """
SELECT DISTINCT ON (bucket, strike) bucket, time AS snapshot_time, strike, value
FROM (
    SELECT date_bin(make_interval(secs => CAST(:bucket AS double precision)), time, TIMESTAMPTZ '2000-01-03 00:00+00') AS bucket,
           time, strike, net_gamma AS value
    FROM gamma_aggregate_items
    WHERE underlying_id = (SELECT id FROM underlyings WHERE symbol = :symbol)
      AND view = :view AND time >= :start AND time < :end
) AS binned
ORDER BY bucket, strike, time DESC
LIMIT 200000
"""
c = build_container()
now = datetime.now(timezone.utc)
for symbol in ("SPX", "NDX", "SPY"):
    for days in (1, 5):
        times = []
        rows = []
        for _ in range(5):
            with c.storage.session_factory() as s:
                s.execute(text("SET TRANSACTION READ ONLY")); s.execute(text("SET LOCAL statement_timeout='60s'"))
                t0 = time.perf_counter()
                rows = s.execute(text(SQL), {"symbol": symbol, "view": "tactical", "start": now - timedelta(days=days), "end": now, "bucket": 120}).all()
                times.append((time.perf_counter() - t0) * 1000)
                s.rollback()
        strikes = {r.strike for r in rows}
        columns = {r.bucket for r in rows}
        approx_bytes = len(strikes) * len(columns) * 9
        print("%-4s %d day(s): rows %6d | strikes %3d | columns %4d | ~JSON %5.0f KB | ms min %.0f median %.0f max %.0f"
              % (symbol, days, len(rows), len(strikes), len(columns), approx_bytes / 1024, min(times), statistics.median(times), max(times)))
