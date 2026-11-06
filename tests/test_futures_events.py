from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.api.routes import futures_anchor_feed, futures_live_feed
from backend.domain.entities import MarketPrice
from backend.domain.use_cases.futures_events import future_for_symbol, translate
from backend.main import app

KEY = {"X-Convexa-Key": "test-key-not-real"}


def test_translation_to_index_and_etf_charts() -> None:
    assert translate("ES", Decimal("7418.50"), Decimal("31.25"), 10.03, None) == {"SPX": Decimal("7387.25"), "SPY": Decimal("736.52")}
    assert translate("NQ", Decimal("27200"), Decimal("110"), None, None) == {"NDX": Decimal("27090.00")}
    assert translate("ES", Decimal("7418.50"), None, 10.03, None) == {}
    assert translate("RTY", Decimal("2730"), None, None, 10.0) == {"IWM": Decimal("273.00")}
    assert [future_for_symbol(s) for s in ("SPX", "spy", "NDX", "QQQ", "IWM", "NVDA")] == ["ES", "ES", "NQ", "NQ", "RTY", None]


@pytest.fixture
def client(tmp_path, monkeypatch):
    key_file = tmp_path / "key.txt"
    key_file.write_text("test-key-not-real", encoding="ascii")
    monkeypatch.setattr(futures_anchor_feed, "get_settings", lambda: SimpleNamespace(ninjatrader_anchor_key_file=str(key_file)))
    monkeypatch.setattr(futures_live_feed, "future_level_offset", lambda storage, future, index: Decimal("31.25"))
    with TestClient(app) as test_client:
        yield test_client


def test_an_es_absorption_is_stored_and_drawn_on_spx_and_spy(client) -> None:
    storage = client.app.state.container.storage
    now = datetime.now(UTC)
    storage.save_market_price(MarketPrice("SPX", now - timedelta(seconds=2), Decimal("7387.00"), 0))
    storage.save_market_price(MarketPrice("SPY", now - timedelta(seconds=2), Decimal("736.50"), 0))
    event = {"symbol": "MES", "contract": "MES 12-26", "time": now.isoformat(), "type": "absorption", "side": "buy", "price": 7418.5, "volume": 236, "detail": "refills 7, held 64 s"}
    assert client.post("/api/v1/feeds/futures-events", json={"events": [event]}).status_code == 401
    result = client.post("/api/v1/feeds/futures-events", json={"events": [event, dict(event, type="bogus")]}, headers=KEY).json()
    assert result["saved"] == 1 and len(result["skipped"]) == 1

    spx = client.get("/api/v1/order-flow-events/SPX").json()["events"]
    assert len(spx) == 1 and spx[0]["price"] == 7387.25 and spx[0]["future"] == "ES" and spx[0]["side"] == "buy"
    spy = client.get("/api/v1/order-flow-events/SPY").json()["events"]
    assert spy[0]["price"] == pytest.approx(7387.25 / (7387.00 / 736.50), abs=0.01)
    assert client.get("/api/v1/order-flow-events/NDX").json()["events"] == []
    assert client.get("/api/v1/order-flow-events/NVDA").json()["events"] == []
