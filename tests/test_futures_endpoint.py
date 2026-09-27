from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient

from backend.domain.entities import MarketPrice
from backend.domain.use_cases import calculate_session_open
from backend.main import app


def test_future_opening_price_get_returns_null_before_anything_is_set() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/futures/ES/opening-price")

    assert response.status_code == 200
    payload = response.json()
    assert payload["symbol"] == "ES"
    assert payload["proxy_symbol"] == "SPX"
    assert payload["opening_price"] is None


def test_future_opening_price_get_returns_404_for_a_non_future_symbol() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/futures/AAPL/opening-price")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_future_opening_price_put_then_get_roundtrips() -> None:
    with TestClient(app) as client:
        put_response = client.put(
            "/api/v1/futures/ES/opening-price", json={"opening_price": "5812.25"}
        )
        get_response = client.get("/api/v1/futures/ES/opening-price")

    assert put_response.status_code == 200
    assert put_response.json()["opening_price"] == 5812.25
    assert get_response.status_code == 200
    assert get_response.json()["opening_price"] == 5812.25


def test_price_history_endpoint_synthesizes_es_from_the_spx_proxy_once_anchored() -> None:
    # ES has no working ThetaData price stream/OHLC/EOD endpoint at all
    # (see PRICE_PROXY_SYMBOL_BY_FUTURE's own docstring) -- its chart is
    # SPX's own real, already-streaming price history shifted by a
    # constant offset = the owner's entered anchor minus SPX's own price
    # at that same 9:30 ET open.
    with TestClient(app) as client:
        storage = client.app.state.container.storage
        session_now = datetime.combine(date.today(), time(19, 0), UTC)
        spx_open = session_now - timedelta(minutes=10)
        storage.save_market_price(MarketPrice(symbol="SPX", as_of=spx_open, price=Decimal(5800), volume=0))
        storage.save_market_price(
            MarketPrice(symbol="SPX", as_of=session_now, price=Decimal(5810), volume=0)
        )
        session_date = calculate_session_open(session_now).date()
        storage.set_future_price_anchor("ES", session_date, Decimal("5812.25"))
        response = client.get("/api/v1/market/ES/history")

    assert response.status_code == 200
    payload = response.json()
    assert payload["symbol"] == "ES"
    # offset = 5812.25 (anchor) - 5800 (SPX's own price at its first
    # reading this session) = 12.25, applied to every SPX point.
    assert len(payload["points"]) == 2
    assert payload["points"][0]["price"] == 5812.25
    assert payload["points"][1]["price"] == 5822.25


def test_price_history_endpoint_returns_empty_for_es_when_no_anchor_set_yet() -> None:
    with TestClient(app) as client:
        storage = client.app.state.container.storage
        session_now = datetime.combine(date.today(), time(19, 0), UTC)
        storage.save_market_price(MarketPrice(symbol="SPX", as_of=session_now, price=Decimal(5800), volume=0))
        response = client.get("/api/v1/market/ES/history")

    assert response.status_code == 200
    assert response.json()["points"] == []


def test_vwap_history_endpoint_shifts_spx_own_proxy_vwap_for_es() -> None:
    # SPX's own VWAP already borrows SPY's volume (VWAP_PROXY_SYMBOL_BY_
    # INDEX) -- ES reuses that exact series (get_vwap_history_async(...,
    # "SPX") recursively), just shifted by the same offset
    # get_price_history_async used above, not a second VWAP formula.
    with TestClient(app) as client:
        storage = client.app.state.container.storage
        session_now = datetime.combine(date.today(), time(19, 0), UTC)
        spx_open = session_now - timedelta(minutes=10)
        # SPX's own single reading's `as_of` is what bounds the SPY
        # window get_vwap_history_async("SPX") itself fetches (session_
        # open through SPX's own latest as_of) -- timestamped at
        # session_now, the same as SPY's second reading below, so that
        # window includes both SPY points rather than cutting the second
        # one off (see test_vwap_history_endpoint_approximates_from_the_
        # proxy_etf_for_ndx's identical shape).
        storage.save_market_price(MarketPrice(symbol="SPX", as_of=session_now, price=Decimal(5800), volume=0))
        storage.save_market_price(
            MarketPrice(symbol="SPY", as_of=spx_open, price=Decimal(580), volume=800)
        )
        storage.save_market_price(
            MarketPrice(symbol="SPY", as_of=session_now, price=Decimal(590), volume=1000)
        )
        session_date = calculate_session_open(session_now).date()
        storage.set_future_price_anchor("ES", session_date, Decimal("5812.25"))
        response = client.get("/api/v1/market/ES/vwap-history")

    assert response.status_code == 200
    payload = response.json()
    assert payload["symbol"] == "ES"
    assert payload["not_applicable"] is False
    # SPX's own VWAP series: first point 5800 (ratio 1), second point
    # ratio (580*800+590*200)/1000 / 580 -> 5800 * 582/580 = 5820. Offset
    # (anchor 5812.25 - SPX's own open 5800 = 12.25) applied to both.
    assert len(payload["points"]) == 2
    assert payload["points"][0]["value"] == 5812.25
    assert payload["points"][1]["value"] == 5832.25
