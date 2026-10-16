"""READ-ONLY: the ES VWAP series the API serves, sampled at fixed minutes, to compare with a running VWAP computed from Yahoo's ES 1-minute bars."""
import asyncio
import sys
from datetime import datetime
from zoneinfo import ZoneInfo
sys.path.insert(0, "V:/Convexa/repo")
from backend.core.container import build_container
from backend.domain.use_cases.read_models import get_vwap_history_async
ET = ZoneInfo("America/New_York")
c = build_container()


async def main():
    series, _ = await get_vwap_history_async(c.async_market_storage, "ES")
    last_by_min = {}
    for t, v in series:
        last_by_min[t.astimezone(ET).strftime("%H:%M")] = float(v)
    for m in ("09:31", "09:32", "09:33", "09:34", "09:35", "09:36", "09:38", "09:40", "09:45", "09:50", "10:00", "10:15", "10:30", "10:45"):
        print("ES_CONVEXA", m, round(last_by_min.get(m, float("nan")), 2))
asyncio.run(main())
