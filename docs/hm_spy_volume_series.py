"""READ-ONLY: how SPY's stored session volume (market_snapshots.volume) moved today -- every change of value, with the time."""
import sys
from datetime import datetime
from zoneinfo import ZoneInfo
sys.path.insert(0, "V:/Convexa/repo")
from backend.core.container import build_container
from backend.domain.use_cases.calculate_anchored_vwap import calculate_session_open
ET = ZoneInfo("America/New_York")
c = build_container()
now = datetime.now(ET)
for sym in ("SPY", "QQQ"):
    rows = c.storage.get_price_history(sym, calculate_session_open(now).replace(hour=4, minute=0), now)
    prev = None
    ch = []
    for r in rows:
        if prev is None or r.volume != prev:
            ch.append((r.as_of.astimezone(ET).strftime("%H:%M:%S"), r.volume))
            prev = r.volume
    print(sym, "readings since 04:00:", len(rows), "| value changes:", len(ch))
    print("  first 6:", ch[:6])
    print("  last 8:", ch[-8:])
