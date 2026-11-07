"""READ-ONLY. Why IWM's GEX profile vanishes when the heatmap is switched on (Lester 2026-11-06 22:25). In-process (TestClient, session gate
overridden): for IWM and SPY (control), views 0dte_volume / 0dte / tactical, the heatmap's fast (10 s) and cycle (60 s) answers for today, and what
the profile would get from them (frontend profileBarsFromHeatmap: per strike the latest non-null value in the last 12 columns), next to /profile."""
import sys

sys.path.insert(0, "V:/Convexa/repo")
from fastapi.testclient import TestClient

from backend.api.deps import require_admin, require_session, require_session_ws
from backend.core.sessions import SessionPayload
from backend.main import create_app

app = create_app()
fake = SessionPayload(user_id=0, username="measure", is_admin=False)
for dep in (require_session, require_session_ws, require_admin):
    app.dependency_overrides[dep] = lambda: fake
START, END = "2026-11-06T13:30:00Z", "2026-11-07T03:15:00Z"


def bars(body):
    cols = body.get("columns") or []
    strikes = body.get("strikes") or []
    recent = cols[-12:]
    n = 0
    for i in range(len(strikes)):
        if any((c.get("values") or [None] * len(strikes))[i] is not None for c in recent):
            n += 1
    last_nonnull = [c.get("time") for c in cols if any(v is not None for v in (c.get("values") or []))]
    return len(cols), len(strikes), n, (str(cols[-1].get("time")) + " lastvalue=" + str(last_nonnull[-1] if last_nonnull else None) if cols else None)


with TestClient(app) as client:
    for symbol in ("IWM", "SPY", "QQQ"):
        for view in ("0dte_volume", "0dte"):
            line = [f"{symbol} {view}:"]
            for source, bucket in (("fast", 10), ("cycle", 60)):
                r = client.get(f"/api/v1/gamma/{symbol}/heatmap", params={"view": view, "metric": "gamma", "start": START, "end": END, "bucket_seconds": bucket, "source": source})
                if r.status_code != 200:
                    line.append(f"{source} HTTP {r.status_code}")
                    continue
                cols, strikes, with_value, last = bars(r.json())
                line.append(f"{source} cols={cols} strikes={strikes} profile_bars={with_value} last={last}")
            p = client.get(f"/api/v1/gamma/{symbol}/profile", params={"view": view})
            items = p.json().get("items") if p.status_code == 200 else None
            line.append(f"profile HTTP {p.status_code} items={len(items) if items is not None else '-'}")
            print(" | ".join(line))
