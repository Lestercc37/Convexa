"""READ-ONLY. SPX vs SPY minute by minute today (last reading of each minute, readings per minute, SPX/SPY ratio) and the same ratio yesterday:
a stale or wrong SPX feed shows as a drifting ratio. Also the ES anchor offset (anchor - SPX open reference)."""
import sys
from datetime import datetime, time, timedelta

sys.path.insert(0, "V:/Convexa/repo")
sys.stdout.reconfigure(encoding="utf-8")
from backend.core.container import build_container
from backend.domain.use_cases.market_hours import EASTERN_TIME

c = build_container()
now = datetime.now(EASTERN_TIME)


def per_minute(symbol, start, end):
    hist = c.storage.get_price_history(symbol, start, end)
    out = {}
    for h in hist:
        key = h.as_of.astimezone(EASTERN_TIME).strftime("%H:%M")
        n = out.get(key, (None, 0))[1]
        out[key] = (float(h.price), n + 1)
    return out


for day, label in ((now.date() - timedelta(days=1), "yesterday"), (now.date(), "today")):
    start = datetime.combine(day, time(9, 29), EASTERN_TIME)
    end = min(now, datetime.combine(day, time(16, 0), EASTERN_TIME)) if label == "today" else datetime.combine(day, time(10, 30), EASTERN_TIME)
    spx, spy = per_minute("SPX", start, end), per_minute("SPY", start, end)
    print(f"--- {label} {day}")
    for key in sorted(spx):
        if key in spy:
            print(f"{key} SPX {spx[key][0]:.2f} (n={spx[key][1]:3d}) SPY {spy[key][0]:.2f} (n={spy[key][1]:3d}) ratio {spx[key][0] / spy[key][0]:.4f}")
