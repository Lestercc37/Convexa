"""READ-ONLY (with clip + error vs Yahoo ES VWAP): how jumpy is the SPX proxy VWAP line today? Per-minute VWAP and the largest minute-to-minute moves for (a) the code now deployed, (b) the pre-10-16 formula, (c) a version that spreads each real volume increment over the readings since the previous real row (time-faithful)."""
import sys
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo
sys.path.insert(0, "V:/Convexa/repo")
from backend.core.container import build_container
from backend.domain.use_cases.calculate_anchored_vwap import calculate_proxy_anchored_vwap_series, calculate_session_open, opening_reference
ET = ZoneInfo("America/New_York")
c = build_container()
now = datetime.now(ET)
op = calculate_session_open(now)
spx = c.storage.get_price_history("SPX", op, now)
spy = c.storage.get_price_history("SPY", op, now)
spy = sorted(spy, key=lambda r: r.as_of)
stale = spy[0].volume
idx_open = opening_reference(spx)[0]
spy_open = opening_reference(spy)[0]


def per_minute(points):
    out = {}
    for t, v in points:
        out[t.astimezone(ET).strftime("%H:%M")] = float(v)
    return out


# (a) deployed code
a = per_minute(calculate_proxy_anchored_vwap_series(spx, spy, now))
# (b) old formula
pv = Decimal(0); vv = 0; prev = 0; b_pts = []
for r in spy:
    iv = max(r.volume - prev, 0); prev = r.volume
    pv += r.price * iv; vv += iv
    if vv:
        b_pts.append((r.as_of, idx_open * (pv / vv) / spy_open))
b = per_minute(b_pts)
# (c) time-faithful: spread each real volume increment evenly over the readings since the previous real row
real_idx = [i for i, r in enumerate(spy) if r.volume != stale]
pv = Decimal(0); vv = 0; last_i = -1; last_vol = 0; c_pts = []
weights = [0] * len(spy)
for i in real_idx:
    d = spy[i].volume - last_vol
    if d > 0:
        n = i - last_i
        share = d / n
        for k in range(last_i + 1, i + 1):
            weights[k] += share
        last_vol = spy[i].volume
    last_i = i
pv = Decimal(0); vv = Decimal(0)
for i, r in enumerate(spy):
    w = Decimal(str(weights[i]))
    pv += r.price * w; vv += w
    if vv > 0:
        c_pts.append((r.as_of, idx_open * (pv / vv) / spy_open))
cc = per_minute(c_pts)


def jumps(series):
    ks = sorted(series)
    d = [(abs(series[k2] - series[k1]), k2) for k1, k2 in zip(ks, ks[1:])]
    d.sort(reverse=True)
    return round(d[0][0], 2), d[0][1], round(sum(x for x, _ in d) / len(d), 2), sum(1 for x, _ in d if x > 1.5)


for name, s in (("deployed", a), ("old formula", b), ("spread (time-faithful)", cc)):
    mx, when, mean, n15 = jumps(s)
    print("%-24s minutes %3d | largest minute-to-minute move %.2f pts at %s | mean %.2f | moves > 1.5 pts: %d" % (name, len(s), mx, when, mean, n15))
for m in ("09:45", "10:00", "10:15", "10:30", "10:45", "11:00", "11:15", "11:30"):
    print(m, "deployed %.2f | old %.2f | spread %.2f" % (a.get(m, float('nan')), b.get(m, float('nan')), cc.get(m, float('nan'))))


# ---- (d) proxy weights with the #287 clip (cap each real increment at 3x the running median of the increments so far, warm-up 4)
import statistics
YAHOO_ES = {"09:35": 7796.43, "09:40": 7793.38, "09:50": 7791.57, "10:00": 7789.42, "10:15": 7787.3, "10:30": 7786.13, "10:45": 7786.23, "11:00": 7786.41, "11:15": 7784.11, "11:30": 7781.02}
ANCHOR = 7802.5
OFFSET_FOR_ES = ANCHOR - float(idx_open)
inc = []
seen = []
pv = Decimal(0); vv = Decimal(0); last_vol = 0; clip_pts = []
for r in spy:
    if r.volume != stale and r.volume > last_vol:
        d_ = r.volume - last_vol; last_vol = r.volume
        cap = 3 * statistics.median(seen) if len(seen) >= 4 else None
        w = min(d_, cap) if cap else d_
        seen.append(d_)
        pv += r.price * Decimal(str(w)); vv += Decimal(str(w))
        clip_pts.append((r.as_of, idx_open * (pv / vv) / spy_open))
cl = per_minute(clip_pts)
mx, when, mean, n15 = jumps(cl)
print("%-24s minutes %3d | largest minute-to-minute move %.2f pts at %s | mean %.2f | moves > 1.5 pts: %d" % ("clipped (#287)", len(cl), mx, when, mean, n15))
def es_err(series):
    errs = []
    for m, y in YAHOO_ES.items():
        k = max((x for x in series if x <= m), default=None)
        if k: errs.append(series[k] + OFFSET_FOR_ES - float(idx_open) + float(idx_open) - 0 if False else (float(idx_open) * 0 + series[k] - float(idx_open) + ANCHOR) - y)
    return errs
for name, s in (("deployed", a), ("old formula", b), ("clipped (#287)", cl)):
    e = es_err(s)
    print("ES VWAP error vs Yahoo ES (%s): %-16s mean abs %.2f | max abs %.2f | %s" % (",".join(YAHOO_ES), name, sum(abs(x) for x in e) / len(e), max(abs(x) for x in e), [round(x, 1) for x in e]))
