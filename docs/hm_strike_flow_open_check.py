"""READ-ONLY. Flow chain 5 s per-strike flush at the open (PR #397): minute_strike_flow rows per minute 09:30-10:15 per symbol today vs 11-05,
freshness of the newest minute, pg_stat_statements for the INSERT if available, and the timing of one live get_strike_flow_summary per symbol."""
import asyncio
import statistics
import sys
import time as clock
from datetime import date, datetime, time, timedelta

sys.path.insert(0, "V:/Convexa/repo")
sys.stdout.reconfigure(encoding="utf-8")
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from backend.core.container import build_container
from backend.core.settings import get_settings
from backend.domain.use_cases.market_hours import EASTERN_TIME
from backend.domain.use_cases.strike_flow import session_bounds

SYMBOLS = ("SPX", "NDX", "SPY", "QQQ", "IWM", "NVDA")
TODAY = date(2026, 11, 6)
PREV = date(2026, 11, 5)


async def main():
    engine = create_async_engine(get_settings().database_url)
    async with engine.connect() as conn:
        for day in (PREV, TODAY):
            a = datetime.combine(day, time(9, 30), EASTERN_TIME)
            b = datetime.combine(day, time(10, 15), EASTERN_TIME)
            rows = (await conn.execute(text(
                "SELECT symbol, count(*) AS n, count(DISTINCT minute) AS m FROM minute_strike_flow WHERE minute >= :a AND minute < :b GROUP BY symbol ORDER BY symbol"),
                {"a": a, "b": b})).all()
            print(day, "09:30-10:15:", " | ".join(f"{r.symbol} {r.n} rows / {r.m} min ({r.n / max(r.m, 1):.0f}/min)" for r in rows))
        now = datetime.now(EASTERN_TIME)
        fresh = (await conn.execute(text(
            "SELECT symbol, max(minute) AS last, max(minute) FILTER (WHERE minute >= date_trunc('minute', now())) AS cur FROM minute_strike_flow WHERE minute >= :a GROUP BY symbol ORDER BY symbol"),
            {"a": now - timedelta(minutes=10)})).all()
        print("now", now.strftime("%H:%M:%S"), "newest minute per symbol:", " | ".join(f"{r.symbol} {r.last.astimezone(EASTERN_TIME):%H:%M} (current minute {'yes' if r.cur else 'no'})" for r in fresh))
        try:
            stats = (await conn.execute(text(
                "SELECT calls, mean_exec_time, total_exec_time FROM pg_stat_statements WHERE query ILIKE 'INSERT INTO minute_strike_flow%' ORDER BY calls DESC LIMIT 3"))).all()
            print("pg_stat_statements INSERT:", [(r.calls, round(r.mean_exec_time, 2), round(r.total_exec_time / 1000, 1)) for r in stats] or "no row")
        except Exception as error:
            print("pg_stat_statements not available:", type(error).__name__)
    await engine.dispose()

    c = build_container()
    storage = c.async_market_storage
    start, end = session_bounds(datetime.now(EASTERN_TIME), partial_minute=True)
    for symbol in ("SPX", "NDX", "QQQ", "SPY"):
        times = []
        for _ in range(5):
            t0 = clock.perf_counter()
            expirations = await storage.get_strike_flow_expirations(symbol, start, end)
            out = await storage.get_strike_flow_summary(symbol, start, end, expirations[:1] or None)
            times.append((clock.perf_counter() - t0) * 1000)
        print(f"{symbol} strike summary median {statistics.median(times):.0f} ms (min {min(times):.0f}, max {max(times):.0f}), {len(out)} rows")


asyncio.run(main())
