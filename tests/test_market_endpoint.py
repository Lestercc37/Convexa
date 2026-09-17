from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from backend.adapters.providers.mock.provider import MockDataProvider
from backend.domain.entities import GammaAggregate, MarketPrice
from backend.domain.use_cases import calculate_pin_risk_score, calculate_time_to_close_pct
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


def test_market_endpoint_marks_anchored_vwap_not_applicable_for_pure_indices() -> None:
    # SPX always reports volume=0 from ThetaData's own index snapshot
    # endpoint (confirmed live, 2026-09-17) -- structurally never
    # computable, not "still accumulating". A nonzero volume here (which
    # would never happen for a real index) is deliberate: proves
    # not_applicable wins on symbol kind alone, not on whatever volume
    # happens to be in the reading.
    price_as_of = datetime(2026, 8, 3, 14, 31, tzinfo=UTC)
    gamma_as_of = datetime(2026, 8, 3, 14, 30, tzinfo=UTC)

    with TestClient(app) as client:
        storage = client.app.state.container.storage
        storage.save_market_price(
            MarketPrice(symbol="SPX", as_of=price_as_of, price=Decimal(5500), volume=1000)
        )
        storage.save_gamma_aggregate(
            GammaAggregate(
                symbol="SPX",
                as_of=gamma_as_of,
                gamma_flip=Decimal(5500),
                call_wall=Decimal(5600),
                put_wall=Decimal(5400),
                absolute_gamma_strike=Decimal(5550),
                net_gamma=Decimal(100),
            )
        )
        storage.save_chain_snapshot(MockDataProvider().get_option_chain("SPX"))
        response = client.get("/api/v1/market/SPX")

    assert response.status_code == 200
    anchored_vwap = response.json()["anchored_vwap"]
    assert anchored_vwap["not_applicable"] is True
    assert anchored_vwap["value"] is None
    assert anchored_vwap["provisional"] is False


def test_vwap_history_endpoint_seeds_a_full_series_not_just_the_latest_point() -> None:
    # This is the fix for VWAP resetting to empty on every remount/symbol
    # switch (Lester's report, 2026-09-17): the frontend should be able
    # to seed vwapPoints from this endpoint the same way it already
    # seeds pricePoints from /market/{symbol}/history.
    with TestClient(app) as client:
        storage = client.app.state.container.storage
        # Relative to the real "now" (this endpoint uses the real clock,
        # same as /market/{symbol}/history), never a fixed wall-clock
        # hour -- avoids the test spuriously running before today's real
        # 09:30 ET session open, which would exclude both readings.
        now = datetime.now(UTC)
        storage.save_market_price(
            MarketPrice(symbol="SPY", as_of=now - timedelta(minutes=10), price=Decimal(550), volume=800)
        )
        storage.save_market_price(
            MarketPrice(symbol="SPY", as_of=now - timedelta(minutes=5), price=Decimal(560), volume=1000)
        )
        response = client.get("/api/v1/market/SPY/vwap-history")

    assert response.status_code == 200
    payload = response.json()
    assert payload["symbol"] == "SPY"
    assert payload["not_applicable"] is False
    assert len(payload["points"]) == 2
    assert payload["points"][0]["value"] == 550
    # (550*800 + 560*200) / 1000 = 552 -- same weighted formula as the
    # single-value endpoint, just kept as a running series.
    assert payload["points"][1]["value"] == 552


def test_vwap_history_endpoint_marks_not_applicable_for_pure_indices() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/market/SPX/vwap-history")

    assert response.status_code == 200
    payload = response.json()
    assert payload["not_applicable"] is True
    assert payload["points"] == []


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
    excluded_keys = {"expected_move", "anchored_vwap", "atr_range", "closing_dynamics"}
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
        "not_applicable": False,
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
    # No items on this hand-built GammaAggregate (only the OI/proximity/
    # gamma-independent time component of Pin Risk Score can be computed —
    # see calculate_closing_dynamics), and charm/vanna_exposure both
    # default to 0 (the documented neutral edge case).
    expected_time_to_close_pct = calculate_time_to_close_pct(price_as_of)
    expected_pin_score, _ = calculate_pin_risk_score((), Decimal(550), expected_time_to_close_pct)
    assert payload["closing_dynamics"] == {
        "active": False,
        "time_to_close_pct": pytest.approx(float(expected_time_to_close_pct)),
        "pin_score": pytest.approx(float(expected_pin_score)),
        "magnet_strike": None,
        "charm_regime": None,
        "vanna_interpretation": None,
        "max_pain": 0,
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
