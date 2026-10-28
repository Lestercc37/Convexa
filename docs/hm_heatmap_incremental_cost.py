"""READ-ONLY. API CPU of the heatmap's fast INCREMENTAL poll (from the last 10 s bucket) vs a FULL poll, in-process (TestClient, session gate overridden),
for SPX / NDX / SPY, views 0dte and tactical, on today's stored fast rows. Median of 5 calls. Milliseconds only."""
import statistics
import sys
import time

sys.path.insert(0, "V:/Convexa/repo")
from fastapi.testclient import TestClient

from backend.api.deps import require_admin, require_session, require_session_ws
from backend.core.sessions import SessionPayload
from backend.main import create_app

app = create_app()
fake = SessionPayload(user_id=0, username="measure", is_admin=False)
for dep in (require_session, require_session_ws, require_admin):
    app.dependency_overrides[dep] = lambda: fake
with TestClient(app) as client:
    for s, bucket in (("SPX", 10), ("NDX", 30), ("SPY", 10)):
        for view in ("0dte", "tactical"):
            out = []
            for label, start in (("full", "2026-10-27T13:30:00Z"), ("incremental", "2026-10-27T19:59:50Z")):
                runs = []
                for _ in range(5):
                    t0, c0 = time.perf_counter(), time.process_time()
                    r = client.get(f"/api/v1/gamma/{s}/heatmap", params={"view": view, "metric": "gamma", "start": start, "end": "2026-10-27T20:01:00Z", "bucket_seconds": bucket, "source": "fast"})
                    runs.append(((time.perf_counter() - t0) * 1000, (time.process_time() - c0) * 1000, len(r.content)))
                out.append(f"{label} {statistics.median(x[1] for x in runs):.0f} ms cpu / {statistics.median(x[0] for x in runs):.0f} ms wall / {runs[0][2] // 1024} KB")
            print(f"{s} {view}: " + " | ".join(out))
