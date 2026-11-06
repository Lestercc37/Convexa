from __future__ import annotations

from fastapi.testclient import TestClient

from backend.api.deps import require_session
from backend.core.sessions import SessionPayload
from backend.main import app


def test_each_trader_keeps_their_own_chart_template() -> None:
    with TestClient(app) as client:
        assert client.get("/api/v1/auth/me/chart-settings").json() == {"schema_version": 1, "settings": {}}
        saved = {"heatmap": True, "heatmapMetric": "absolute_gamma", "profile": True, "timeframe": "5m"}
        assert client.put("/api/v1/auth/me/chart-settings", json={"settings": saved}).status_code == 200
        assert client.get("/api/v1/auth/me/chart-settings").json()["settings"] == saved

        app.dependency_overrides[require_session] = lambda: SessionPayload(user_id=7, username="teammate1", is_admin=False)
        try:
            assert client.get("/api/v1/auth/me/chart-settings").json()["settings"] == {}
        finally:
            app.dependency_overrides[require_session] = lambda: SessionPayload(user_id=0, username="test-user", is_admin=True)

        assert client.put("/api/v1/auth/me/chart-settings", json={"settings": [1, 2]}).status_code == 422
        assert client.put("/api/v1/auth/me/chart-settings", json={"settings": {"x": "a" * 40_000}}).status_code == 413
