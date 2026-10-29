"""READ-ONLY. Dumps, for the VWAP comparison of one session: (1) the shadow per-trade VWAP lines of stream_processor_worker.log ("stream VWAP (shadow)"),
(2) the dashboard's anchored VWAP series (GET /market/{symbol}/vwap-history, in-process TestClient, session gate overridden for this process only) for
SPY / QQQ / IWM / NVDA / SPX / NDX, (3) the scheduler's latest stored session volume per symbol. Prints one JSON object."""
import json
import re
import sys

sys.path.insert(0, "V:/Convexa/repo")
from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.api.deps import require_admin, require_session, require_session_ws
from backend.core.container import build_container
from backend.core.sessions import SessionPayload
from backend.main import create_app

day = sys.argv[1] if len(sys.argv) > 1 else "2026-10-28"
shadow = []
pattern = re.compile(r"^(\d{4}-\d\d-\d\d) (\d\d:\d\d:\d\d).*stream VWAP \(shadow\): (.*)$")
with open("V:/Convexa/repo/logs/stream_processor_worker.log", encoding="utf-8", errors="replace") as handle:
    for line in handle:
        m = pattern.match(line.strip())
        if m and m.group(1) == day:
            entry = {"t": m.group(2)}
            for part in m.group(3).split("; "):
                pm = re.match(r"(\w+) ([\d.]+) \(([\d,]+) sh, ([\d,]+) trades", part)
                if pm:
                    entry[pm.group(1)] = [float(pm.group(2)), int(pm.group(3).replace(",", "")), int(pm.group(4).replace(",", ""))]
            shadow.append(entry)
app = create_app()
fake = SessionPayload(user_id=0, username="measure", is_admin=False)
for dep in (require_session, require_session_ws, require_admin):
    app.dependency_overrides[dep] = lambda: fake
dash = {}
with TestClient(app) as client:
    for s in ("SPY", "QQQ", "IWM", "NVDA", "SPX", "NDX"):
        body = client.get(f"/api/v1/market/{s}/vwap-history").json()
        per_minute = {}
        for p in body.get("points", []):
            per_minute[p["timestamp"][:16]] = p["value"]
        dash[s] = {"proxy": body.get("proxy_symbol"), "points": per_minute}
c = build_container()
vols = {}
with c.storage.session_factory() as s:
    s.execute(text("SET TRANSACTION READ ONLY"))
    for sym in ("SPY", "QQQ", "IWM", "NVDA"):
        vols[sym] = s.execute(text("""select max(p.volume) from market_snapshots p join underlyings u on u.id = p.underlying_id
            where u.symbol = :s and (p.time at time zone 'America/New_York')::date = cast(:d as date)"""), {"s": sym, "d": day}).scalar()
    s.rollback()
print(json.dumps({"shadow": shadow, "dash": dash, "scheduler_volume": {k: (int(v) if v is not None else None) for k, v in vols.items()}}))
