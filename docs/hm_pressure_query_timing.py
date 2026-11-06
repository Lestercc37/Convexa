"""READ-ONLY. Timing of the per-minute strike-flow query behind the NinjaTrader pressure heatmap (nt_feed._pressure_lines) on today's data:
SPX / QQQ / IWM, 0DTE expiration, 09:30 -> now. Prints rows, distinct strikes, minutes and the median of 5 runs."""
import asyncio
import statistics
import sys
import time as clock
from datetime import datetime, time

sys.path.insert(0, "V:/Convexa/repo")
sys.stdout.reconfigure(encoding="utf-8")
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from backend.core.settings import get_settings
from backend.domain.use_cases.market_hours import EASTERN_TIME

SQL = """SELECT minute, strike, SUM(delta_flow) AS flow FROM minute_strike_flow
WHERE symbol = :symbol AND minute >= :start AND minute < :end AND (CAST(:all_expirations AS boolean) OR expiration = ANY(:expirations))
GROUP BY minute, strike"""


async def main():
    engine = create_async_engine(get_settings().database_url)
    now = datetime.now(EASTERN_TIME)
    start = datetime.combine(now.date(), time(9, 30), EASTERN_TIME)
    async with engine.connect() as conn:
        for symbol in ("SPX", "QQQ", "IWM"):
            runs = []
            rows = []
            for _ in range(5):
                t0 = clock.perf_counter()
                rows = (await conn.execute(text(SQL), {"symbol": symbol, "start": start, "end": now, "all_expirations": False, "expirations": [now.date()]})).all()
                runs.append((clock.perf_counter() - t0) * 1000)
            print(f"{symbol}: {len(rows)} rows, {len({r.strike for r in rows})} strikes, {len({r.minute for r in rows})} minutes, median {statistics.median(runs):.0f} ms (max {max(runs):.0f})")
    await engine.dispose()


asyncio.run(main())
