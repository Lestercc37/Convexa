"""READ-ONLY: what the API serves as the ES and NQ VWAP line right now (same function as GET /market/{symbol}/vwap-history), and the typed anchors."""
import asyncio
import sys
from datetime import datetime
from zoneinfo import ZoneInfo
sys.path.insert(0, "V:/Convexa/repo")
from backend.core.container import build_container
from backend.domain.use_cases.read_models import get_vwap_history_async
from backend.domain.use_cases.calculate_anchored_vwap import calculate_session_open
ET = ZoneInfo("America/New_York")
c = build_container()
now = datetime.now(ET)
day = calculate_session_open(now).date()


async def main():
    for fut in ("ES", "NQ"):
        try:
            anchor = await c.async_market_storage.get_future_price_anchor(fut, day)
        except Exception as e:
            anchor = "ERR " + type(e).__name__
        series, na = await get_vwap_history_async(c.async_market_storage, fut)
        pts = [(t.astimezone(ET).strftime("%H:%M:%S"), round(float(v), 2)) for t, v in series]
        print(fut, "typed anchor today:", anchor, "| points", len(pts), "| first", pts[:2], "| last", pts[-2:], "| not_applicable", na)
asyncio.run(main())
