"""READ-ONLY. Time and size of the |delta| lookup the minute flow does once a minute per underlying (same SQL as PostgreSQLStorage.get_latest_option_abs_deltas), run on the live database."""
import ctypes
import sys
import time

try:
    ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x00004000)
except Exception:
    pass
sys.path.insert(0, "V:/Convexa/repo")
from sqlalchemy import text
from backend.core.container import build_container
c = build_container()
SQL = """
SELECT DISTINCT ON (oc.occ_symbol) oc.occ_symbol, s.delta
FROM option_chain_snapshots AS s
JOIN option_contracts AS oc ON oc.id = s.contract_id
WHERE s.underlying_id = (SELECT id FROM underlyings WHERE symbol = :symbol)
  AND s.time >= now() - make_interval(mins => :window)
ORDER BY oc.occ_symbol, s.time DESC
"""
for symbol in ("SPX", "NDX", "SPY", "QQQ", "IWM", "NVDA"):
    walls = []
    n = 0
    for _ in range(3):
        with c.storage.session_factory() as s:
            s.execute(text("SET TRANSACTION READ ONLY"))
            t0 = time.perf_counter()
            rows = s.execute(text(SQL), {"symbol": symbol, "window": 3}).all()
            walls.append(time.perf_counter() - t0)
            n = len(rows)
            s.rollback()
    print(f"{symbol:5s} | contracts {n:5d} | seconds per query (3 runs) {', '.join(f'{w:.2f}' for w in walls)}")
