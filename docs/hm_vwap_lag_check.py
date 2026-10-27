"""READ-ONLY. Is the anchored VWAP lagging? For SPY, SPX (proxy SPY) and QQQ: the API's current anchored VWAP (in-process, session check overridden for this process
only), and for SPY the stored readings since 09:30: how often the session volume actually changes (only the scheduler writes it) and the gaps between changes."""
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "V:/Convexa/repo")
from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.api.deps import require_session
from backend.core.container import build_container
from backend.domain.use_cases.market_hours import EASTERN_TIME
from backend.main import app

app.dependency_overrides[require_session] = lambda: None
with TestClient(app) as client:
    for symbol in ("SPY", "SPX", "QQQ"):
        b = client.get(f"/api/v1/market/{symbol}").json()
        v = b.get("anchored_vwap") or {}
        print(f"{symbol}: price {b.get('price')} as_of {b.get('as_of')} | vwap {v.get('value')} provisional {v.get('provisional')} proxy {v.get('proxy_symbol')}")
c = build_container()
with c.storage.session_factory() as s:
    s.execute(text("SET TRANSACTION READ ONLY"))
    rows = s.execute(text("""select p.time, p.price, p.volume from market_snapshots p join underlyings u on u.id = p.underlying_id
        where u.symbol = 'SPY' and p.time >= (date_trunc('day', now() at time zone 'America/New_York') + interval '9 hours 30 minutes') at time zone 'America/New_York'
        order by p.time""")).all()
    s.rollback()
changes, last = [], None
for t, p, vol in rows:
    if vol != last:
        changes.append((t, vol))
        last = vol
gaps = [(b[0] - a[0]).total_seconds() for a, b in zip(changes, changes[1:])]
print(f"SPY readings since 09:30: {len(rows)} | volume changes: {len(changes)} | gap between volume updates: median {sorted(gaps)[len(gaps)//2]:.0f} s, max {max(gaps):.0f} s")
print("last volume updates: " + ", ".join(f"{t.astimezone(EASTERN_TIME):%H:%M:%S} {v:,}" for t, v in changes[-5:]))
print(f"latest reading {rows[-1][0].astimezone(EASTERN_TIME):%H:%M:%S} volume {rows[-1][2]:,}")
