"""READ-ONLY. Saves the heatmap answers the web chart gets for one symbol (fast 10 s and cycle 60 s, today from 08:30 ET to now) as JSON files under
logs/, to replay them through the frontend's own code on the laptop (2026-11-06: IWM's GEX profile empty with the heatmap on). Usage: SYMBOL VIEW."""
import json
import sys
from datetime import datetime, timezone

sys.path.insert(0, "V:/Convexa/repo")
from fastapi.testclient import TestClient

from backend.api.deps import require_admin, require_session, require_session_ws
from backend.core.sessions import SessionPayload
from backend.main import create_app

symbol, view = (sys.argv[1:3] + ["IWM", "0dte_volume"][len(sys.argv[1:3]):])[:2]
app = create_app()
fake = SessionPayload(user_id=0, username="measure", is_admin=False)
for dep in (require_session, require_session_ws, require_admin):
    app.dependency_overrides[dep] = lambda: fake
start = "2026-11-06T13:30:00Z"
end = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
with TestClient(app) as client:
    for source, bucket in (("fast", 10), ("cycle", 60)):
        r = client.get(f"/api/v1/gamma/{symbol}/heatmap", params={"view": view, "metric": "gamma", "start": start, "end": end, "bucket_seconds": bucket, "source": source})
        path = f"V:/Convexa/repo/logs/heatmap_{symbol}_{view}_{source}.json"
        open(path, "w", encoding="utf-8").write(r.text)
        body = r.json()
        print(source, r.status_code, "keys", sorted(body)[:12], "cols", len(body.get("columns") or []), "first col keys", sorted((body.get("columns") or [{}])[0]))
