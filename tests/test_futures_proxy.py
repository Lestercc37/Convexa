"""ES/NQ levels from SPX/NDX, shifted by the owner's anchor (use_cases/futures_proxy.py).

ThetaData has no futures and its "ES" root is Eversource Energy, so ES/NQ read their proxy index's
stored gamma/chain and express every price level in futures points: level + (anchor - the index's
first price of the session)."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from backend.domain.entities import (
    ContractType,
    GammaAggregate,
    GammaAggregateItem,
    MarketPrice,
    OptionChain,
    OptionContract,
    OptionGreeks,
)
from backend.domain.use_cases import calculate_session_open
from backend.domain.use_cases import futures_proxy
from backend.main import app

SESSION_NOW = datetime.combine(date.today(), time(19, 0), UTC)


@pytest.fixture(autouse=True)
def _clear_open_cache() -> None:
    futures_proxy._proxy_open_cache.clear()


def _gamma(symbol: str, base: int) -> GammaAggregate:
    return GammaAggregate(
        symbol=symbol,
        as_of=SESSION_NOW,
        items=(
            GammaAggregateItem(
                strike=Decimal(base),
                total_gamma_exposure=Decimal(10),
                call_gamma_exposure=Decimal(6),
                put_gamma_exposure=Decimal(4),
                net_gamma=Decimal(2),
                contract_count=3,
            ),
        ),
        gamma_flip=Decimal(base - 15),
        call_wall=Decimal(base + 40),
        put_wall=Decimal(base - 50),
        max_pain=Decimal(base - 10),
        absolute_gamma_strike=Decimal(base + 40),
        net_gamma=Decimal(12345),
        peak_gamma_value=Decimal(99),
    )


def _chain(symbol: str, spot: int) -> OptionChain:
    greeks = OptionGreeks(*(Decimal(0) for _ in range(6)))
    return OptionChain(
        symbol=symbol,
        as_of=SESSION_NOW,
        spot_price=Decimal(spot),
        contracts=(
            OptionContract(
                underlying=symbol,
                strike=Decimal(spot),
                expiration=SESSION_NOW.date() + timedelta(days=1),
                contract_type=ContractType.CALL,
                occ_symbol=f"{symbol}261007C00{spot}000",
                bid=Decimal(1),
                ask=Decimal(2),
                last=Decimal(1),
                volume=1,
                open_interest=1,
                iv=Decimal("0.2"),
                greeks=greeks,
            ),
        ),
    )


def _seed(storage, proxy: str, spot: int, future: str | None = None, anchor: int | None = None) -> None:
    storage.save_market_price(MarketPrice(symbol=proxy, as_of=SESSION_NOW - timedelta(minutes=10), price=Decimal(spot), volume=0))
    storage.save_market_price(MarketPrice(symbol=proxy, as_of=SESSION_NOW, price=Decimal(spot + 10), volume=0))
    storage.save_gamma_aggregate(_gamma(proxy, spot))
    storage.save_chain_snapshot(_chain(proxy, spot))
    if future is not None and anchor is not None:
        storage.set_future_price_anchor(future, calculate_session_open(SESSION_NOW).date(), Decimal(anchor))


def test_es_gamma_is_spxs_aggregate_in_es_points() -> None:
    with TestClient(app) as client:
        storage = client.app.state.container.storage
        _seed(storage, "SPX", 5800, "ES", 5850)  # basis = +50

        response = client.get("/api/v1/gamma/ES")

    assert response.status_code == 200
    body = response.json()
    gamma = body["gamma"] if "gamma" in body else body
    assert gamma["symbol"] == "ES"
    assert gamma["gamma_flip"] == 5785 + 50
    assert gamma["call_wall"] == 5840 + 50
    assert gamma["put_wall"] == 5750 + 50
    assert gamma["max_pain"] == 5790 + 50
    assert gamma["net_gamma"] == 12345, "exposures are the index's own, not shifted"


def test_es_gamma_has_no_levels_until_todays_anchor_is_entered() -> None:
    with TestClient(app) as client:
        storage = client.app.state.container.storage
        _seed(storage, "SPX", 5800)  # no anchor for ES

        response = client.get("/api/v1/gamma/ES/flip")

    assert response.status_code == 200
    assert response.json()["flip_found"] is False


def test_nq_levels_come_from_ndx_with_its_own_anchor() -> None:
    with TestClient(app) as client:
        storage = client.app.state.container.storage
        _seed(storage, "NDX", 20500, "NQ", 20560)  # basis = +60

        response = client.get("/api/v1/gamma/NQ/flip")

    assert response.status_code == 200
    assert response.json()["gamma_flip_price"] == 20485 + 60


def test_es_and_nq_anchors_are_independent() -> None:
    with TestClient(app) as client:
        storage = client.app.state.container.storage
        _seed(storage, "SPX", 5800, "ES", 5850)
        _seed(storage, "NDX", 20500)  # NQ never anchored

        es = client.get("/api/v1/gamma/ES/flip")
        nq = client.get("/api/v1/gamma/NQ/flip")

    assert es.json()["flip_found"] is True
    assert nq.json()["flip_found"] is False


def test_es_chain_is_spxs_chain_in_es_points() -> None:
    with TestClient(app) as client:
        storage = client.app.state.container.storage
        _seed(storage, "SPX", 5800, "ES", 5850)

        response = client.get("/api/v1/chain/ES")

    assert response.status_code == 200
    chain = response.json()
    assert chain["symbol"] == "ES"
    assert chain["spot_price"] == 5800 + 50
    assert chain["contracts"][0]["strike"] == 5800 + 50


def test_es_chain_without_an_anchor_says_why_instead_of_serving_unshifted_strikes() -> None:
    with TestClient(app) as client:
        storage = client.app.state.container.storage
        _seed(storage, "SPX", 5800)

        response = client.get("/api/v1/chain/ES")

    assert response.status_code == 404
    assert "opening price" in response.text


def test_gamma_aggregate_shift_keeps_unset_levels_unset() -> None:
    gamma = GammaAggregate(symbol="SPX", as_of=SESSION_NOW)  # no flip/walls, max_pain 0
    shifted = futures_proxy.shift_gamma_aggregate(gamma, "ES", Decimal(50))
    assert shifted.symbol == "ES"
    assert shifted.gamma_flip is None and shifted.call_wall is None and shifted.put_wall is None
    assert shifted.max_pain == 0 and shifted.absolute_gamma_strike == 0


def test_the_proxy_opening_price_is_read_once_per_session() -> None:
    class _Storage:
        history_reads = 0

        def get_latest_price(self, symbol):
            return MarketPrice(symbol=symbol, as_of=SESSION_NOW, price=Decimal(5810), volume=0)

        def get_future_price_anchor(self, symbol, session_date):
            return Decimal(5850)

        def get_price_history(self, symbol, start, end):
            type(self).history_reads += 1
            return [MarketPrice(symbol=symbol, as_of=start, price=Decimal(5800), volume=0)]

    storage = _Storage()
    first = futures_proxy.future_level_offset(storage, "ES", "SPX")
    second = futures_proxy.future_level_offset(storage, "ES", "SPX")

    assert first == second == Decimal(50)
    assert _Storage.history_reads == 1


def test_es_and_nq_are_futures_with_an_index_proxy() -> None:
    from backend.domain.entities import UnderlyingKind
    from backend.domain.underlyings import ACTIVE_UNDERLYINGS_BY_SYMBOL

    assert futures_proxy.PRICE_PROXY_SYMBOL_BY_FUTURE == {"ES": "SPX", "NQ": "NDX"}
    for symbol in ("ES", "NQ"):
        assert ACTIVE_UNDERLYINGS_BY_SYMBOL[symbol].kind == UnderlyingKind.FUTURE


def test_es_market_is_spxs_snapshot_in_es_points() -> None:
    with TestClient(app) as client:
        storage = client.app.state.container.storage
        _seed(storage, "SPX", 5800, "ES", 5850)  # session open 5800 -> basis = +50, latest SPX 5810

        response = client.get("/api/v1/market/ES")

    assert response.status_code == 200
    body = response.json()
    market = body["market"] if "market" in body else body
    assert market["symbol"] == "ES"
    assert market["price"] == 5810 + 50, "the price shown is SPX's price plus the owner's basis, never Eversource's stored rows"
    assert market["call_wall"] == 5840 + 50
    assert market["gamma_flip"] == 5785 + 50


def test_nq_market_needs_no_row_of_its_own_and_uses_its_own_anchor() -> None:
    with TestClient(app) as client:
        storage = client.app.state.container.storage
        _seed(storage, "NDX", 20500, "NQ", 20560)  # basis = +60, latest NDX 20510

        response = client.get("/api/v1/market/NQ")

    assert response.status_code == 200
    body = response.json()
    market = body["market"] if "market" in body else body
    assert market["symbol"] == "NQ"
    assert market["price"] == 20510 + 60


def test_es_market_without_todays_anchor_says_no_data_and_serves_no_price() -> None:
    with TestClient(app) as client:
        storage = client.app.state.container.storage
        _seed(storage, "SPX", 5800)  # no anchor for ES today
        # an old Eversource-style row stored under "ES" must never be served as the E-mini
        storage.save_market_price(MarketPrice(symbol="ES", as_of=SESSION_NOW - timedelta(days=2), price=Decimal("64.95"), volume=0))

        response = client.get("/api/v1/market/ES")

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "NO_OPENING_PRICE"
    assert "No data for ES" in error["message"]
    assert "64.95" not in response.text


def test_es_market_without_anchor_does_not_affect_the_index_itself() -> None:
    with TestClient(app) as client:
        storage = client.app.state.container.storage
        _seed(storage, "SPX", 5800)

        response = client.get("/api/v1/market/SPX")

    assert response.status_code == 200


def test_market_snapshot_shift_moves_levels_and_keeps_widths() -> None:
    from backend.domain.entities import AtrRange, ClosingDynamics, ExpectedMove, MarketSnapshot

    snapshot = MarketSnapshot(
        symbol="SPX",
        as_of=SESSION_NOW,
        price=Decimal(5810),
        volume=0,
        gamma=_gamma("SPX", 5800),
        expected_move=ExpectedMove(
            implied_1sd_dollars=Decimal(40), implied_1sd_pct=Decimal("0.7"), remaining_1sd_dollars=Decimal(30),
            remaining_1sd_pct=Decimal("0.5"), upper_bound=Decimal(5840), lower_bound=Decimal(5760), atm_iv=Decimal("0.2"),
        ),
        atr_range=AtrRange(
            atr=Decimal(60), atr_provisional=False, daily_bars_count=20, today_open=Decimal(5800), bands_provisional=False,
            outer_upper_band=Decimal(5860), outer_lower_band=Decimal(5740), inner_upper_band=Decimal(5830), inner_lower_band=Decimal(5770),
        ),
        closing_dynamics=ClosingDynamics(
            time_to_close_pct=Decimal("0.5"), active=False, pin_score=Decimal(1), magnet_strike=Decimal(5800),
            charm_regime=None, vanna_interpretation=None, max_pain=Decimal(5790),
        ),
    )

    shifted = futures_proxy.shift_market_snapshot(snapshot, "ES", Decimal(50))

    assert shifted.symbol == "ES" and shifted.price == 5860
    assert shifted.expected_move.upper_bound == 5890 and shifted.expected_move.lower_bound == 5810
    assert shifted.expected_move.implied_1sd_dollars == 40, "widths are the index's own"
    assert shifted.atr_range.atr == 60 and shifted.atr_range.today_open == 5850
    assert shifted.atr_range.outer_upper_band == 5910 and shifted.atr_range.inner_lower_band == 5820
    assert shifted.closing_dynamics.magnet_strike == 5850 and shifted.closing_dynamics.max_pain == 5840
    assert shifted.recent_flow == ()
