from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient

from backend.domain.entities import MarketPrice
from backend.domain.use_cases import calculate_session_open
from backend.main import app


def test_future_opening_price_get_returns_null_before_anything_is_set(monkeypatch) -> None:
    monkeypatch.setattr("backend.api.routes.futures._now", lambda: _at(TUESDAY, 10, 0))
    with TestClient(app) as client:
        _spx_prints(client, _at(TUESDAY, 9, 30, 2))
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


TUESDAY = date(2026, 10, 13)           # a normal trading day; EDT, so 09:30 ET = 13:30 UTC


def _at(day: date, hour: int, minute: int, second: int = 0) -> datetime:
    """A wall-clock instant given in ET (EDT, UTC-4, valid for these October dates)."""
    return datetime(day.year, day.month, day.day, hour + 4, minute, second, tzinfo=UTC)


def _spx_prints(client: TestClient, *instants: datetime) -> None:
    for index, instant in enumerate(instants):
        client.app.state.container.storage.save_market_price(
            MarketPrice(symbol="SPX", as_of=instant, price=Decimal(5800 + index), volume=0)
        )


def test_future_opening_price_put_then_get_roundtrips(monkeypatch) -> None:
    monkeypatch.setattr("backend.api.routes.futures._now", lambda: _at(TUESDAY, 9, 31, 10))
    with TestClient(app) as client:
        _spx_prints(client, _at(TUESDAY, 9, 30, 2))                      # today's first SPX price
        put_response = client.put(
            "/api/v1/futures/ES/opening-price", json={"opening_price": "5812.25"}
        )
        get_response = client.get("/api/v1/futures/ES/opening-price")

    assert put_response.status_code == 200
    assert put_response.json()["opening_price"] == 5812.25
    assert put_response.json()["session_date"] == "2026-10-13"
    assert put_response.json()["saved_at"] is not None, "the screen shows which session and when it was saved"
    assert get_response.status_code == 200
    assert get_response.json()["opening_price"] == 5812.25
    assert get_response.json()["accepting"] is True


def test_before_the_open_the_number_is_refused_and_yesterdays_is_not_shown(monkeypatch) -> None:
    """9:15 ET: SPX's latest price is still yesterday's close. A save used to land on YESTERDAY's session."""
    monkeypatch.setattr("backend.api.routes.futures._now", lambda: _at(TUESDAY, 9, 15))
    monday = date(2026, 10, 12)
    with TestClient(app) as client:
        _spx_prints(client, _at(monday, 9, 30, 2), _at(monday, 16, 0))
        client.app.state.container.storage.set_future_price_anchor("ES", monday, Decimal("5799.00"))

        got = client.get("/api/v1/futures/ES/opening-price")
        refused = client.put("/api/v1/futures/ES/opening-price", json={"opening_price": "5812.25"})
        monday_after = client.app.state.container.storage.get_future_price_anchor("ES", monday)

    assert got.json()["opening_price"] is None, "yesterday's number must not be shown as today's"
    assert got.json()["session_date"] == "2026-10-13"
    assert got.json()["accepting"] is False and got.json()["waiting_reason"] == "before_open"
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "OPENING_PRICE_NOT_OPEN_YET"
    assert "9:30:00" in refused.json()["error"]["message"]
    assert monday_after == Decimal("5799.00"), "the refused number must not overwrite yesterday's session"


def test_after_9_30_but_before_spxs_first_price_the_number_is_refused(monkeypatch) -> None:
    monkeypatch.setattr("backend.api.routes.futures._now", lambda: _at(TUESDAY, 9, 30, 1))
    monday = date(2026, 10, 12)
    with TestClient(app) as client:
        _spx_prints(client, _at(monday, 16, 0))
        refused = client.put("/api/v1/futures/ES/opening-price", json={"opening_price": "5812.25"})

    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "OPENING_PRICE_NOT_OPEN_YET"


def test_a_late_entry_after_the_close_is_saved_for_that_same_day(monkeypatch) -> None:
    monkeypatch.setattr("backend.api.routes.futures._now", lambda: _at(TUESDAY, 16, 40))
    with TestClient(app) as client:
        _spx_prints(client, _at(TUESDAY, 9, 30, 2), _at(TUESDAY, 16, 0))
        saved = client.put("/api/v1/futures/ES/opening-price", json={"opening_price": "5812.25"})

    assert saved.status_code == 200
    assert saved.json()["session_date"] == "2026-10-13"


def test_on_a_weekend_the_last_session_stays_open_for_a_correction(monkeypatch) -> None:
    saturday = date(2026, 10, 17)
    friday = date(2026, 10, 16)
    monkeypatch.setattr("backend.api.routes.futures._now", lambda: _at(saturday, 12, 0))
    with TestClient(app) as client:
        _spx_prints(client, _at(friday, 9, 30, 2), _at(friday, 16, 0))
        saved = client.put("/api/v1/futures/ES/opening-price", json={"opening_price": "5812.25"})

    assert saved.status_code == 200
    assert saved.json()["session_date"] == "2026-10-16", "saved for Friday's session, the one on screen"


def test_opening_price_window_rule() -> None:
    from backend.domain.use_cases.futures_proxy import opening_price_window

    monday_close = _at(date(2026, 10, 12), 16, 0)
    cases = [
        # (now, latest SPX print, expected accepting, expected reason, expected session)
        (_at(TUESDAY, 9, 15), monday_close, False, "before_open", TUESDAY),
        (_at(TUESDAY, 9, 29, 59), monday_close, False, "before_open", TUESDAY),
        (_at(TUESDAY, 9, 30, 0), monday_close, False, "waiting_first_price", TUESDAY),
        (_at(TUESDAY, 9, 30, 3), _at(TUESDAY, 9, 30, 2), True, None, TUESDAY),
        (_at(TUESDAY, 10, 0), None, False, "waiting_first_price", TUESDAY),
        (_at(TUESDAY, 16, 30), _at(TUESDAY, 16, 0), True, None, TUESDAY),
        (_at(date(2026, 10, 17), 12, 0), _at(date(2026, 10, 16), 16, 0), True, None, date(2026, 10, 16)),
        (_at(date(2026, 10, 18), 12, 0), None, False, "waiting_first_price", date(2026, 10, 18)),
    ]
    for now, latest, accepting, reason, session in cases:
        window = opening_price_window(now, latest)
        assert (window.accepting, window.waiting_reason, window.session_date) == (accepting, reason, session), (now, latest)


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
