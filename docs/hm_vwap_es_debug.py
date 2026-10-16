"""READ-ONLY: why does today's ES anchored VWAP differ from Thinkorswim's? First readings of SPX and SPY today, the proxy VWAP series, and the ES offset."""
import asyncio
import sys
from datetime import datetime, time as tod
from zoneinfo import ZoneInfo
sys.path.insert(0, "V:/Convexa/repo")
from backend.core.container import build_container
from backend.domain.use_cases.calculate_anchored_vwap import calculate_anchored_vwap_series, calculate_proxy_anchored_vwap_series, calculate_session_open
ET = ZoneInfo("America/New_York")
c = build_container()
now = datetime.now(ET)
op = calculate_session_open(now)
st = c.storage
spx = st.get_price_history("SPX", op, now)
spy = st.get_price_history("SPY", op, now)
print("session open", op.astimezone(ET).strftime("%H:%M:%S"), "| SPX readings", len(spx), "| SPY readings", len(spy))
for name, rows in (("SPX", spx), ("SPY", spy)):
    print(name, "first 8 (time, price, volume):", [(r.as_of.astimezone(ET).strftime("%H:%M:%S"), float(r.price), r.volume) for r in rows[:8]])
    vols = [r.volume for r in rows]
    dec = sum(1 for a, b in zip(vols, vols[1:]) if b < a)
    print(name, "volume decreases between consecutive readings:", dec, "| last volume", vols[-1] if vols else None)
series = calculate_anchored_vwap_series(spy, now)
print("SPY own VWAP series: first", [(t.astimezone(ET).strftime("%H:%M:%S"), round(float(v), 3)) for t, v in series[:3]], "last", [(t.astimezone(ET).strftime("%H:%M:%S"), round(float(v), 3)) for t, v in series[-2:]])
prox = calculate_proxy_anchored_vwap_series(spx, spy, now)
print("SPX proxy VWAP: first", [(t.astimezone(ET).strftime("%H:%M:%S"), round(float(v), 2)) for t, v in prox[:3]], "last", [(t.astimezone(ET).strftime("%H:%M:%S"), round(float(v), 2)) for t, v in prox[-2:]])
print("SPY open price used:", float(spy[0].price) if spy else None, "| SPX open price used:", float(spx[0].price) if spx else None)
# independent: plain volume-weighted average of SPY readings using volume deltas
num = den = 0.0
prev = None
for r in spy:
    if prev is not None and r.volume > prev.volume:
        d = r.volume - prev.volume
        num += float(r.price) * d
        den += d
    prev = r
print("independent SPY VWAP from volume deltas:", round(num / den, 3) if den else None, "| ratio to SPY open %.5f" % (num / den / float(spy[0].price)) if den else "")
import sqlalchemy as sa
with st.session_factory() as s:
    s.execute(sa.text("SET TRANSACTION READ ONLY"))
    rows = s.execute(sa.text("SELECT date, price FROM future_price_anchors a JOIN underlyings u ON u.id=a.underlying_id WHERE u.symbol='ES' ORDER BY date DESC LIMIT 3")).all() if False else []
