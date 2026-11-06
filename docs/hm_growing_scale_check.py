"""READ-ONLY. Fixed 'growing' heatmap scale for ES 0DTE Volumen (Lester 2026-11-05): reference = p98 of the PREVIOUS session's last column
(SPX, view 0dte_volume, cycle source, one-minute buckets). Prints, for each session, the column p98 at a few moments of the day as a share of that
reference: how bright the map would be at 09:31 / 10:00 / 11:00 / 13:00 / 15:59 under the fixed scale. In-process (TestClient, session gate overridden)."""
import sys

sys.path.insert(0, "V:/Convexa/repo")
sys.stdout.reconfigure(encoding="utf-8")
from fastapi.testclient import TestClient

from backend.api.deps import require_admin, require_session, require_session_ws
from backend.core.sessions import SessionPayload
from backend.main import create_app

app = create_app()
fake = SessionPayload(user_id=0, username="measure", is_admin=False)
for dep in (require_session, require_session_ws, require_admin):
    app.dependency_overrides[dep] = lambda: fake

# (previous session, session) pairs; 16:00 ET = 21:00Z after 11-01 (EST), 20:00Z before.
PAIRS = [("2026-10-30", "2026-11-02"), ("2026-11-02", "2026-11-03"), ("2026-11-03", "2026-11-04"), ("2026-11-04", "2026-11-05")]


def close_utc(day):
    return f"{day}T{'21' if day >= '2026-11-02' else '20'}:00:00Z"


def open_utc(day):
    return f"{day}T{'14' if day >= '2026-11-02' else '13'}:30:00Z"


def p98(values):
    mags = sorted(abs(v) for v in values if v is not None)
    return mags[min(len(mags) - 1, int(0.98 * len(mags)))] if mags else None


MOMENTS = ["09:31", "09:45", "10:00", "10:30", "11:00", "12:00", "13:00", "14:00", "15:00", "15:59"]
DAYS = ["2026-10-29", "2026-10-30", "2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05"]


def et_clock(iso, day):
    hh, mm = int(iso[11:13]), iso[14:16]
    return f"{hh - (5 if day >= '2026-11-02' else 4):02d}:{mm}"


# Second idea: colour the 0DTE VOLUME column against the 0DTE OI column of the SAME minute. Gamma per contract is the same in both (same strike,
# same moment), so the ratio is volume against open interest: it cannot blow up into the close the way gamma does.
with TestClient(app) as client:
    for day in DAYS:
        p98s = {}
        for view in ("0dte", "0dte_volume"):
            r = client.get("/api/v1/gamma/SPX/heatmap", params={"view": view, "metric": "gamma", "start": open_utc(day), "end": close_utc(day), "bucket_seconds": 60})
            p98s[view] = {et_clock(c["as_of"], day): p98(c["values"]) for c in r.json().get("columns", [])}
        cells = []
        for m in MOMENTS:
            vol, oi = p98s["0dte_volume"].get(m), p98s["0dte"].get(m)
            cells.append(f"{m} {vol / oi * 100:4.0f}%" if vol and oi else f"{m}  n/a")
        print(day + ": " + " | ".join(cells))
