"""VXN 1-minute bars from ThetaData (Lester 2026-11-05: "puedes descargar VXN para el NDX"), one request per weekday, saved to
logs/vxn_minute_bars.csv (no database writes); then the NDX-per-volatility-point table of docs/hm_vix_spx_study.py part 1 with VXN
next to VIX on the same minutes (NDX = last stored price of each minute, market_snapshots). VXN has no listed options: price only.
usage: hm_vxn_download_and_ndx.py 2026-09-02 2026-11-05"""
import csv
import json
import statistics
import sys
import time as clock
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, "V:/Convexa/repo")
from sqlalchemy import text

from backend.core.container import build_container

ET = ZoneInfo("America/New_York")
start, end = date.fromisoformat(sys.argv[1]), date.fromisoformat(sys.argv[2])
out = Path("V:/Convexa/repo/logs/vxn_minute_bars.csv")
vxn = defaultdict(dict)  # day -> minute idx -> close
day = start
requests = 0
if out.exists() and "--reuse" in sys.argv:
    # The file of an earlier run: nothing is asked to ThetaData again.
    with out.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            stamp = datetime.fromisoformat(row["timestamp_et"])
            vxn[stamp.date()][stamp.hour * 60 + stamp.minute - 570] = float(row["close"])
    day = end + timedelta(days=1)
if day <= end:
    with out.open("w", newline="", encoding="utf-8") as handle:
      writer = csv.writer(handle)
      writer.writerow(["timestamp_et", "open", "high", "low", "close"])
      while day <= end:
          if day.weekday() < 5:
              url = f"http://localhost:25503/v3/index/history/ohlc?symbol=VXN&start_date={day:%Y%m%d}&end_date={day:%Y%m%d}&interval=1m&format=json"
              try:
                  with urllib.request.urlopen(url, timeout=30) as response:
                      rows = json.loads(response.read()).get("response", [])
              except Exception as exc:  # noqa: BLE001 -- a holiday answers with an error; the day is simply skipped
                  rows = []
                  print(f"{day}: {type(exc).__name__}")
              requests += 1
              for row in rows:
                  stamp = datetime.fromisoformat(row["timestamp"])
                  idx = stamp.hour * 60 + stamp.minute - 570
                  if 0 <= idx < 390 and row["close"]:
                      vxn[day][idx] = float(row["close"])
                      writer.writerow([row["timestamp"], row["open"], row["high"], row["low"], row["close"]])
              clock.sleep(0.3)
          day += timedelta(days=1)
print(f"VXN: {requests} requests, {sum(len(v) for v in vxn.values())} minute bars over {len(vxn)} days -> {out}")

c = build_container()
with c.storage.session_factory() as s:
    s.execute(text("SET TRANSACTION READ ONLY"))
    rows = s.execute(text(
        """select u.symbol, date_trunc('minute', m.time), (array_agg(m.price order by m.time desc))[1]
           from market_snapshots m join underlyings u on u.id = m.underlying_id
           where u.symbol in ('VIX', 'NDX') and m.time >= :a
             and (m.time at time zone 'America/New_York')::time >= '09:30' and (m.time at time zone 'America/New_York')::time < '16:00'
           group by 1, 2"""), {"a": datetime.combine(start, datetime.min.time(), ET)}).all()
stored = defaultdict(lambda: defaultdict(dict))
for sym, minute, price in rows:
    local = minute.astimezone(ET)
    stored[local.date()][sym][local.hour * 60 + local.minute - 570] = float(price)
days = sorted(d for d in vxn if len(vxn[d]) >= 190 and len(stored[d]["NDX"]) >= 300 and len(stored[d]["VIX"]) >= 300)
print(f"days with >= 190 minutes of VXN and >= 300 of VIX and NDX: {len(days)}")


def at(by, idx, back=10):  # VXN misses ~30 % of minutes (close 0): last value up to 10 minutes back
    for k in range(idx, idx - back - 1, -1):
        if k in by:
            return by[k]
    return None


def pct(values, q):
    s = sorted(values)
    k = (len(s) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def ols0(xs, ys):
    sxx = sum(x * x for x in xs)
    b = sum(x * y for x, y in zip(xs, ys)) / sxx
    my = statistics.mean(ys)
    sst = sum((y - my) ** 2 for y in ys)
    return b, 1 - sum((y - b * x) ** 2 for x, y in zip(xs, ys)) / sst


MIN_MOVE = {5: 0.10, 15: 0.15, 60: 0.30, 390: 0.50}
print("NDX per point of:   window  n   slope   R2  | ratio n  median [p25, p75]   | %/pt  | opposite")
for vol in ("VXN", "VIX"):
    for w in (5, 15, 60, 390):
        pairs = []
        for d in days:
            series = vxn[d] if vol == "VXN" else stored[d]["VIX"]
            # The day window starts at 09:35: VXN's 09:30 bar has no close (open 0.0), as VIX's.
            for a in ([5] if w == 390 else range(0, 390 - w + 1, w)):
                b = 389 if w == 390 else a + w
                v0, v1, n0, n1 = at(series, a), at(series, b), at(stored[d]["NDX"], a), at(stored[d]["NDX"], b)
                if None not in (v0, v1, n0, n1):
                    pairs.append((v1 - v0, n1 - n0, (n1 - n0) / n0 * 100, v0))
        if not pairs:
            continue
        slope, r2 = ols0([p[0] for p in pairs], [p[1] for p in pairs])
        big = [p for p in pairs if abs(p[0]) >= MIN_MOVE[w]]
        ratios = [p[1] / p[0] for p in big]
        moved = [p for p in pairs if p[0] and p[1]]
        opp = sum(1 for p in moved if (p[0] > 0) != (p[1] > 0)) / len(moved)
        label = "day" if w == 390 else f"{w}m"
        print(f"  {vol}               {label:5} {len(pairs):5} {slope:+7.1f} {r2:4.2f} | {len(ratios):4} {statistics.median(ratios):+7.1f} [{pct(ratios, .25):+7.1f}, {pct(ratios, .75):+7.1f}] "
              f"| {statistics.median([p[2] / p[0] for p in big]):+.3f} | {opp:5.1%}")
        if w == 15:
            lo, hi = (20, 25) if vol == "VXN" else (17, 20)
            for name, sub in ((f"<{lo}", [p for p in big if p[3] < lo]), (f"{lo}-{hi}", [p for p in big if lo <= p[3] < hi]),
                              (f"{hi}+", [p for p in big if p[3] >= hi])):
                if len(sub) >= 3:
                    r = [p[1] / p[0] for p in sub]
                    print(f"        start {name:6} n={len(r):4} median {statistics.median(r):+7.1f} [{pct(r, .25):+7.1f}, {pct(r, .75):+7.1f}]")
first = days[0] if days else None
if first:
    print(f"VXN level range in the sample: {min(min(vxn[d].values()) for d in days):.2f} .. {max(max(vxn[d].values()) for d in days):.2f}")
