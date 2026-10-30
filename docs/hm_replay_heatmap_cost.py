"""READ-ONLY (Pre-Session 5-day replay design, 2026-10-29): what one 5-session heatmap costs, per symbol, as the API would build it (get_gamma_heatmap_async +
gamma_heatmap_response): server time, strikes x columns, JSON size. Tactical view, net gamma, 60 s and 120 s buckets, the 5 sessions ending 2026-10-29."""
import asyncio
import json
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, "V:/Convexa/repo")
from backend.api.serializers import gamma_heatmap_response
from backend.core.container import build_container
from backend.domain.use_cases.read_models import get_gamma_heatmap_async

ET = ZoneInfo("America/New_York")
START, END = datetime(2026, 10, 23, 9, 25, tzinfo=ET), datetime(2026, 10, 29, 16, 5, tzinfo=ET)


async def main() -> None:
    c = build_container()
    for symbol in ("SPX", "NDX", "QQQ"):
        for bucket in (60, 120):
            t0 = time.perf_counter()
            heatmap = await get_gamma_heatmap_async(c.async_market_storage, symbol, "tactical", "gamma", START, END, bucket, "cycle")
            body = json.dumps(gamma_heatmap_response(heatmap), default=str)
            elapsed = time.perf_counter() - t0
            print(f"{symbol} bucket {bucket}s: {len(heatmap.strikes)} strikes x {len(heatmap.columns)} columns | {len(body) / 1e6:.2f} MB JSON | server {elapsed:.1f} s")


asyncio.run(main())
