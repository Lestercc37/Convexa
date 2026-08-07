from datetime import UTC, datetime
from decimal import Decimal

from fastapi.testclient import TestClient

from backend.adapters.providers.mock.provider import MockDataProvider
from backend.domain.entities import GammaAggregate, MarketPrice
from backend.main import app


def test_market_endpoint_reads_persisted_snapshot() -> None:
    with TestClient(app) as client:
        missing = client.get("/api/v1/market/spy")
        client.post("/internal/trigger-calculation/spy")
        response = client.get("/api/v1/market/spy")

    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "NOT_FOUND"
    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == 1
    assert payload["symbol"] == "SPY"
    assert payload["price"] == 552.25
    assert payload["dealer_mode"] in {"long_gamma", "short_gamma"}
    assert payload["dealer_mode_source"] in {"agree", "price_vs_flip"}
    assert isinstance(payload["dealer_mode_confirmed"], bool)
    assert payload["gamma_as_of"]


def test_market_endpoint_confirms_agreeing_dealer_mode_at_gamma_flip() -> None:
    price_as_of = datetime(2026, 8, 3, 14, 31, tzinfo=UTC)
    gamma_as_of = datetime(2026, 8, 3, 14, 30, tzinfo=UTC)

    with TestClient(app) as client:
        storage = client.app.state.container.storage
        storage.save_market_price(
            MarketPrice(
                symbol="SPY",
                as_of=price_as_of,
                price=Decimal(550),
                volume=1000,
            )
        )
        storage.save_gamma_aggregate(
            GammaAggregate(
                symbol="SPY",
                as_of=gamma_as_of,
                gamma_flip=Decimal(550),
                call_wall=Decimal(560),
                put_wall=Decimal(540),
                absolute_gamma_strike=Decimal(555),
                net_gamma=Decimal(100),
            )
        )
        storage.save_chain_snapshot(MockDataProvider().get_option_chain("SPY"))
        response = client.get("/api/v1/market/SPY")

    assert response.status_code == 200
    payload = response.json()
    excluded_keys = {"expected_move", "anchored_vwap", "atr_range"}
    assert {key: payload[key] for key in payload if key not in excluded_keys} == {
        "schema_version": 1,
        "symbol": "SPY",
        "as_of": "2026-08-03T14:31:00Z",
        "price": 550,
        "volume": 1000,
        "gamma_flip": 550,
        "call_wall": 560,
        "put_wall": 540,
        "absolute_gamma_strike": 555,
        "dealer_mode": "long_gamma",
        "dealer_mode_source": "agree",
        "dealer_mode_confirmed": True,
        "gamma_as_of": "2026-08-03T14:30:00Z",
    }
    assert payload["expected_move"]["atm_iv"] == 0.18
    assert payload["anchored_vwap"] == {
        "value": 550,
        "provisional": False,
        "anchor_time": "2026-08-03T13:30:00Z",
        "sample_count": 1,
    }
    # No daily_bars saved: ATR itself is provisional, but today's open is
    # still known from the same market price used above for anchored_vwap —
    # the two provisional signals are independent (see calculate_atr_range).
    assert payload["atr_range"] == {
        "atr": None,
        "atr_provisional": True,
        "daily_bars_count": 0,
        "today_open": 550,
        "bands_provisional": True,
        "outer_upper_band": None,
        "outer_lower_band": None,
        "inner_upper_band": None,
        "inner_lower_band": None,
    }


def test_market_endpoint_prefers_price_when_dealer_mode_diverges() -> None:
    as_of = datetime(2026, 8, 3, 14, 31, tzinfo=UTC)

    with TestClient(app) as client:
        storage = client.app.state.container.storage
        storage.save_market_price(
            MarketPrice(
                symbol="SPY",
                as_of=as_of,
                price=Decimal(551),
                volume=1000,
            )
        )
        storage.save_gamma_aggregate(
            GammaAggregate(
                symbol="SPY",
                as_of=as_of,
                gamma_flip=Decimal(550),
                net_gamma=Decimal(-100),
            )
        )
        storage.save_chain_snapshot(MockDataProvider().get_option_chain("SPY"))
        response = client.get("/api/v1/market/SPY")

    assert response.status_code == 200
    payload = response.json()
    assert payload["dealer_mode"] == "long_gamma"
    assert payload["dealer_mode_source"] == "price_vs_flip"
    assert payload["dealer_mode_confirmed"] is False


def test_market_endpoint_returns_not_found_when_gamma_is_missing() -> None:
    with TestClient(app) as client:
        client.app.state.container.storage.save_market_price(
            MarketPrice(
                symbol="IWM",
                as_of=datetime(2026, 8, 3, 14, 31, tzinfo=UTC),
                price=Decimal(220),
                volume=1000,
            )
        )
        response = client.get("/api/v1/market/IWM")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_openapi_documents_versioned_market_response_model() -> None:
    with TestClient(app) as client:
        response = client.get("/openapi.json")

    response_schema = response.json()["paths"]["/api/v1/market/{symbol}"]["get"][
        "responses"
    ]["200"]["content"]["application/json"]["schema"]
    assert response_schema["$ref"].endswith("/MarketSnapshotResponse")
