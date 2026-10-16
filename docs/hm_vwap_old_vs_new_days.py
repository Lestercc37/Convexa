"""READ-ONLY: per recent session, SPY's end-of-day VWAP computed (a) the way the code did until 2026-10-16 (volume delta against the previous row, no stale handling) and (b) with the stale-carry fix; prints both so they can be compared with a VWAP from Yahoo's 1-minute bars."""
import sys
from datetime import datetime, time as tod, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo
sys.path.insert(0, "V:/Convexa/repo")
from backend.core.container import build_container
from backend.domain.use_cases.calculate_anchored_vwap import calculate_anchored_vwap_series
ET = ZoneInfo("America/New_York")
c = build_container()
today = datetime.now(ET).date()
days = []
d = today - timedelta(days=1)
while len(days) < 6:
    if d.weekday() < 5:
        days.append(d)
    d -= timedelta(days=1)
for day in sorted(days):
    a = datetime.combine(day, tod(9, 30), tzinfo=ET)
    b = datetime.combine(day, tod(16, 0), tzinfo=ET)
    rows = c.storage.get_price_history("SPY", a, b)
    if not rows:
        print(day, "no rows"); continue
    pv, vv, prev = Decimal(0), 0, 0
    for r in sorted(rows, key=lambda r: r.as_of):
        iv = max(r.volume - prev, 0)
        prev = r.volume
        pv += r.price * iv
        vv += iv
    old = float(pv / vv) if vv else None
    new = calculate_anchored_vwap_series(rows, b)
    openp, closep = float(rows[0].price), float(rows[-1].price)
    print("%s SPY open %.2f close %.2f | OLD formula VWAP %.2f | NEW (stale-aware) VWAP %.2f" % (day, openp, closep, old, float(new[-1][1]) if new else float("nan")))
