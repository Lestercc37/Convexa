"""READ-ONLY: SPY rows carrying the real (non-stale) session volume between 09:30 and 09:50 today: time, price, volume, delta against the previous real row."""
import sys
from datetime import datetime
from zoneinfo import ZoneInfo
sys.path.insert(0, "V:/Convexa/repo")
from backend.core.container import build_container
from backend.domain.use_cases.calculate_anchored_vwap import calculate_session_open
ET = ZoneInfo("America/New_York")
c = build_container()
now = datetime.now(ET)
rows = c.storage.get_price_history("SPY", calculate_session_open(now), now)
stale = rows[0].volume
prev = 0
print("stale constant", stale)
for r in rows:
    hm = r.as_of.astimezone(ET).strftime("%H:%M:%S")
    if hm > "09:50:00":
        break
    if r.volume != stale:
        print(hm, "price", float(r.price), "volume", r.volume, "delta", r.volume - prev)
        prev = r.volume
