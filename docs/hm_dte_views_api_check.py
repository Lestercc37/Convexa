"""READ-ONLY. The live API app in-process (FastAPI TestClient, the session check overridden for this process only) answering the new gamma views: status, view, levels,
heatmap columns. Nothing is written."""
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "V:/Convexa/repo")

from fastapi.testclient import TestClient

from backend.api.deps import require_session
from backend.main import app

app.dependency_overrides[require_session] = lambda: None
start = (datetime.now(timezone.utc) - timedelta(hours=10)).isoformat()
with TestClient(app) as client:
    for symbol, view in [("SPX", "0dte"), ("SPX", "1dte"), ("SPX", "tactical"), ("ES", "0dte"), ("QQQ", "2dte"), ("VIX", "2dte"), ("SPX", "bogus")]:
        r = client.get(f"/api/v1/gamma/{symbol}", params={"view": view})
        body = r.json()
        g = body.get("gamma", body) if r.status_code == 200 else {}
        print(f"{symbol:4s} {view:8s} {r.status_code} view={g.get('view')} has_data={g.get('has_data')} cw={g.get('call_wall')} pw={g.get('put_wall')} flip={g.get('gamma_flip')}"
              if r.status_code == 200 else f"{symbol:4s} {view:8s} {r.status_code} {str(body)[:100]}")
    for source in ("cycle", "fast"):
        r = client.get("/api/v1/gamma/SPX/heatmap", params={"view": "0dte", "metric": "delta", "start": start, "source": source, "bucket_seconds": 60})
        b = r.json()
        print(f"heatmap SPX 0dte {source}: {r.status_code} columns={len(b.get('columns', []))} strikes={len(b.get('strikes', []))}")
