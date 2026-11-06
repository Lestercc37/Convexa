"""READ-ONLY. Stored rows per minute for SPX / NDX / SPY / QQQ in a window, and the longest gap between SPX rows: was the index really silent?
usage: hm_index_gap_check.py 2026-11-05 11:30 12:15"""
import sys
from datetime import date, datetime, time

sys.path.insert(0, "V:/Convexa/repo")
sys.stdout.reconfigure(encoding="utf-8")
from backend.core.container import build_container
from backend.domain.use_cases.market_hours import EASTERN_TIME

c = build_container()
day = date.fromisoformat(sys.argv[1])
h1, m1 = map(int, sys.argv[2].split(":"))
h2, m2 = map(int, sys.argv[3].split(":"))
start = datetime.combine(day, time(h1, m1), EASTERN_TIME)
end = datetime.combine(day, time(h2, m2), EASTERN_TIME)
counts = {}
for symbol in ("SPX", "NDX", "SPY", "QQQ"):
    rows = c.storage.get_price_history(symbol, start, end)
    for r in rows:
        key = r.as_of.astimezone(EASTERN_TIME).strftime("%H:%M")
        counts.setdefault(key, {}).setdefault(symbol, 0)
        counts[key][symbol] += 1
    if symbol in ("SPX", "NDX"):
        gaps = sorted(((b.as_of - a.as_of).total_seconds(), a.as_of.astimezone(EASTERN_TIME).strftime("%H:%M:%S"), float(a.price), float(b.price)) for a, b in zip(rows, rows[1:]))[-3:]
        print(symbol, "longest gaps (s, from, price before, price after):", [(round(g[0]), g[1], g[2], g[3]) for g in gaps])
for key in sorted(counts):
    print(key, " ".join(f"{s} {counts[key].get(s, 0):3d}" for s in ("SPX", "NDX", "SPY", "QQQ")))
