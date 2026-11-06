"""READ-ONLY timing of the requests the dashboard makes when the symbol changes (2026-11-05, Lester: "la demora de la carga de las gráficas cuando
cambias de símbolo"). The API app is built in-process (no network, no Cloudflare, no browser) with the session check replaced by a stub, and every
request is a GET. Measures server time and response size, twice per request (cold / warm: some endpoints cache for a few seconds).
usage: hm_symbol_switch_timings.py SPX NDX SPY"""
import asyncio
import sys
import time
from datetime import datetime, time as dtime

sys.path.insert(0, "V:/Convexa/repo")
sys.stdout.reconfigure(encoding="utf-8")
import httpx  # noqa: E402

from backend.api.deps import require_session  # noqa: E402
from backend.domain.use_cases.market_hours import EASTERN_TIME  # noqa: E402
from backend.main import create_app  # noqa: E402

app = create_app()
app.dependency_overrides[require_session] = lambda: {"sub": "timing-script", "role": "viewer"}
symbols = sys.argv[1:] or ["SPX", "NDX", "SPY"]
day_open = datetime.combine(datetime.now(EASTERN_TIME).date(), dtime(9, 30), EASTERN_TIME).isoformat()
VIEW = "0dte_volume"


def requests_for(sym):
    fast_bucket = 30 if sym == "NDX" else 10
    return [
        ("1 history", f"/api/v1/market/{sym}/history"),
        ("1 vwap-history", f"/api/v1/market/{sym}/vwap-history"),
        ("2 gamma", f"/api/v1/gamma/{sym}?view={VIEW}"),
        ("2 market", f"/api/v1/market/{sym}"),
        ("chain expirations", f"/api/v1/chain/{sym}/expirations"),
        ("chart: profile", f"/api/v1/gamma/{sym}/profile?view={VIEW}"),
        ("chart: heatmap fast (full)", f"/api/v1/gamma/{sym}/heatmap?view={VIEW}&metric=gamma&start={day_open}&bucket_seconds={fast_bucket}&source=fast"),
        ("chart: heatmap cycle history", f"/api/v1/gamma/{sym}/heatmap?view={VIEW}&metric=gamma&start={day_open}&bucket_seconds=60"),
        ("chart: alert cubes (10k)", f"/api/v1/alerts/{sym}?limit=10000&since={day_open}"),
        ("alerts panel", f"/api/v1/alerts/{sym}"),
        ("chart: flow minutes", f"/api/v1/flow/{sym}/minutes?start={day_open}"),
    ]


async def main():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://in-process") as client:
        for sym in symbols:
            print(f"== {sym}")
            for label, url in requests_for(sym):
                cells = []
                for _ in range(2):
                    started = time.perf_counter()
                    response = await client.get(url.replace("+", "%2B"))
                    elapsed = (time.perf_counter() - started) * 1000
                    cells.append((elapsed, len(response.content), response.status_code))
                (t1, size, code), (t2, _, _) = cells
                print(f"  {label:30} {code}  {t1:7.0f} ms / {t2:6.0f} ms  {size / 1024:8.1f} KB")
        print("== VIX pill")
        for url in ("/api/v1/market/VIX", "/api/v1/market/VIX/history"):
            started = time.perf_counter()
            response = await client.get(url)
            print(f"  {url:30} {response.status_code}  {(time.perf_counter() - started) * 1000:7.0f} ms  {len(response.content) / 1024:8.1f} KB")


asyncio.run(main())
