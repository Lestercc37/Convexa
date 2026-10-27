"""READ-ONLY. CPU of GET /market/{symbol} in-process (TestClient, session gate overridden): 5 calls per symbol in a row, first call and the median of the
other four, so the shared few-second cache (PR market cache) shows as first = build, rest = cache hits. Prints milliseconds only."""
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
    for s in ("SPX", "NDX", "SPY", "QQQ", "IWM", "NVDA"):
        runs = []
        for _ in range(5):
            t0, c0 = time.perf_counter(), time.process_time()
            r = client.get(f"/api/v1/market/{s}")
            runs.append(((time.perf_counter() - t0) * 1000, (time.process_time() - c0) * 1000, r.status_code))
        rest = runs[1:]
        print(f"{s}: first {runs[0][0]:.0f} ms wall / {runs[0][1]:.0f} ms cpu | next four median {statistics.median(x[0] for x in rest):.1f} ms wall / {statistics.median(x[1] for x in rest):.1f} ms cpu | status {runs[0][2]}")
