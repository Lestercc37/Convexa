"""READ-ONLY. Replays the stored SPX/SPY and NDX/QQQ prices of several sessions through IndexStaleGuard (backend/domain/use_cases/
index_stale_guard.py, copied next to this script as hm_index_stale_guard_mod.py until it is deployed). Stored rows carry no exchange time,
so only the drift and silence rules are exercised here. Prints every episode (start / end, reason, ratio) and, while active, how far the
derived price is from what the index printed later (the index caught up), per day.
usage: hm_index_stale_guard_replay.py 2026-10-29 2026-10-30 2026-11-02 2026-11-03 2026-11-04 2026-11-05 2026-11-06"""
import sys
from datetime import date, datetime, time

sys.path.insert(0, "V:/Convexa/repo")
sys.path.insert(0, "V:/Convexa/repo/logs")
sys.stdout.reconfigure(encoding="utf-8")
try:
    from backend.domain.use_cases.index_stale_guard import IndexStaleGuard
except ImportError:  # before the deploy: the copy next to this script
    from hm_index_stale_guard_mod import IndexStaleGuard

from backend.core.container import build_container
from backend.domain.use_cases.market_hours import EASTERN_TIME

c = build_container()
for arg in sys.argv[1:]:
    day = date.fromisoformat(arg)
    start = datetime.combine(day, time(9, 30), EASTERN_TIME)
    end = datetime.combine(day, time(16, 0), EASTERN_TIME)
    now_et = datetime.now(EASTERN_TIME)
    if end > now_et:
        end = now_et
    guard = IndexStaleGuard()
    events = []
    for symbol in ("SPX", "SPY", "NDX", "QQQ"):
        for row in c.storage.get_price_history(symbol, start, end):
            events.append((row.as_of, symbol, row.price))
    events.sort(key=lambda e: e[0])
    derived = {"SPX": 0, "NDX": 0}
    for at, symbol, price in events:
        if symbol in ("SPX", "NDX"):
            guard.observe_index(symbol, price, None, at)
        else:
            for index, _ in guard.observe_proxy(symbol, price, at):
                derived[index] += 1
        for change in guard.drain_changes():
            print(f"{day} {change.at.astimezone(EASTERN_TIME):%H:%M:%S} {change.symbol} {'START' if change.active else 'END  '} {change.reason} ratio {change.ratio:.5f}")
    print(f"{day}: {len(events)} rows, derived writes SPX {derived['SPX']} NDX {derived['NDX']}, active at the end SPX {guard.active('SPX')} NDX {guard.active('NDX')}")
