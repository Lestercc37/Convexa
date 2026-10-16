"""READ-ONLY experiment: ES VWAP built from SPY readings with the volume deltas clipped at k x the median delta (blocks), against Yahoo's running ES VWAP (values pasted below, typical price of 1-minute bars)."""
import statistics
import sys
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo
sys.path.insert(0, "V:/Convexa/repo")
from backend.core.container import build_container
from backend.domain.use_cases.calculate_anchored_vwap import calculate_session_open
ET = ZoneInfo("America/New_York")
c = build_container()
now = datetime.now(ET)
rows = c.storage.get_price_history("SPY", calculate_session_open(now), now)
spx = c.storage.get_price_history("SPX", calculate_session_open(now), now)
stale = rows[0].volume
real = []
prev = 0
for r in rows:
    if r.volume != stale and r.volume > prev:
        real.append((r.as_of.astimezone(ET).strftime("%H:%M"), float(r.price), r.volume - prev))
        prev = r.volume
open_spy = float(rows[0].price)
spx_open = float(spx[0].price)
anchor = 7802.5
yahoo = {"09:31": 7798.44, "09:35": 7796.43, "09:38": 7794.73, "09:40": 7793.38, "09:45": 7791.80, "09:50": 7791.57, "10:00": 7789.42, "10:15": 7787.30, "10:30": 7786.13}
med = statistics.median(d for _, _, d in real)
print("real volume rows", len(real), "| median delta", med, "| largest deltas", sorted((d for _, _, d in real), reverse=True)[:3])


def es_at(minute, k):
    pv = vv = 0.0
    for hm, price, d in real:
        if hm > minute:
            break
        dd = min(d, k * med) if k else d
        pv += price * dd
        vv += dd
    return anchor + spx_open * ((pv / vv) / open_spy - 1) if vv else float("nan")


def es_causal(minute, k, warm=4):
    pv = vv = 0.0
    seen = []
    for hm, price, d in real:
        if hm > minute:
            break
        cap = k * statistics.median(seen) if len(seen) >= warm else None
        dd = min(d, cap) if cap else d
        seen.append(d)
        pv += price * dd
        vv += dd
    return anchor + spx_open * ((pv / vv) / open_spy - 1) if vv else float("nan")


for k in (5, 4, 3, 2.5):
    errs = [es_causal(m, k) - y for m, y in yahoo.items()]
    print("CAUSAL clip k=%-4s | errors: %s | mean abs %.2f | max abs %.2f" % (k, [round(e, 1) for e in errs], statistics.mean(abs(e) for e in errs), max(abs(e) for e in errs)))
for k in (None,):
    errs = [es_at(m, k) - y for m, y in yahoo.items()]
    print("clip k=%-4s | error vs Yahoo ES VWAP at %s: %s | mean abs %.2f | max abs %.2f" % (k, ",".join(yahoo), [round(e, 1) for e in errs], statistics.mean(abs(e) for e in errs), max(abs(e) for e in errs)))
