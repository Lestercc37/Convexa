"""READ-ONLY: per symbol, how many of today's market_snapshots rows carry the SAME session volume as the very first row of the session (stale carry-forward) and the shape of the problem."""
import sys
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo
sys.path.insert(0, "V:/Convexa/repo")
from backend.core.container import build_container
from backend.domain.use_cases.calculate_anchored_vwap import calculate_session_open
ET = ZoneInfo("America/New_York")
c = build_container()
now = datetime.now(ET)
op = calculate_session_open(now)
for u in c.storage.list_underlyings():
    sym = u.symbol
    rows = c.storage.get_price_history(sym, op, now)
    if not rows:
        continue
    vols = [r.volume for r in rows]
    cnt = Counter(vols)
    top, n = cnt.most_common(1)[0]
    decs = sum(1 for a, b in zip(vols, vols[1:]) if b < a)
    print("%-6s rows %5d | distinct volumes %4d | most frequent %12d x%5d (%.0f%%) | first %d | last %d | decreases %d" % (sym, len(rows), len(cnt), top, n, 100 * n / len(rows), vols[0], vols[-1], decs))
