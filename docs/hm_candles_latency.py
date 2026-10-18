"""READ-ONLY: latency and size of the multi-session candle endpoint's SQL (AsyncPostgreSQLStorage.get_price_candles) on the real market_snapshots,
5 sessions at 5 minutes (and 1 session / 10 sessions for SPX), 5 repetitions each, through a SYNC read-only session (identical plan). Changes nothing."""
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "V:/Convexa/repo")
from sqlalchemy import text
from backend.core.container import build_container

SQL = """
WITH series AS (
    SELECT id FROM underlyings WHERE symbol = :symbol
), local_prices AS (
    SELECT m.time, m.price, m.time AT TIME ZONE 'America/New_York' AS local_time
    FROM market_snapshots AS m
    WHERE m.underlying_id = (SELECT id FROM series) AND m.time >= :since
), regular AS (
    SELECT * FROM local_prices
    WHERE local_time::time >= TIME '09:30' AND local_time::time < TIME '16:00'
), recent_days AS (
    SELECT DISTINCT local_time::date AS day FROM regular ORDER BY day DESC LIMIT :sessions
)
SELECT date_bin(make_interval(secs => CAST(:bucket AS double precision)), local_time, TIMESTAMP '2000-01-03 09:30') AS bucket_local,
       (array_agg(price ORDER BY time))[1] AS open, max(price) AS high, min(price) AS low, (array_agg(price ORDER BY time DESC))[1] AS close
FROM regular
WHERE local_time::date IN (SELECT day FROM recent_days)
GROUP BY bucket_local
ORDER BY bucket_local
"""
c = build_container()
since = datetime.now(timezone.utc) - timedelta(days=14)
for symbol, sessions in (("SPX", 1), ("SPX", 5), ("SPX", 10), ("NDX", 5), ("SPY", 5), ("QQQ", 5), ("IWM", 5), ("NVDA", 5), ("VIX", 5)):
    times, rows = [], []
    for _ in range(5):
        with c.storage.session_factory() as s:
            s.execute(text("SET TRANSACTION READ ONLY")); s.execute(text("SET LOCAL statement_timeout='60s'"))
            t0 = time.perf_counter()
            rows = s.execute(text(SQL), {"symbol": symbol, "since": since, "sessions": sessions, "bucket": 300}).all()
            times.append((time.perf_counter() - t0) * 1000)
            s.rollback()
    print("%-5s %2d session(s): candles %4d | ms min %.0f median %.0f max %.0f" % (symbol, sessions, len(rows), min(times), statistics.median(times), max(times)))
