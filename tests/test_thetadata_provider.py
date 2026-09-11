from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from datetime import date, datetime, timedelta
from datetime import time as dtime
from decimal import Decimal
from typing import ClassVar

import httpx
import pytest

import backend.adapters.providers.thetadata.provider as provider_module
from backend.adapters.providers.thetadata.provider import (
    DAILY_BARS_CACHE_TTL_SECONDS,
    MARKET_HOLIDAYS_CACHE_TTL_SECONDS,
    THETADATA_MAX_CONCURRENT_REQUESTS,
    ThetaDataProvider,
    ThetaStreamHub,
    _build_occ_symbol,
    _log_req_response,
    _nearest_expiration_cutoff,
    _parse_et_timestamp,
    _roots_for_symbol,
    _time_to_expiration_years,
)
from backend.domain.entities import ContractType, MarketHolidayType, UnderlyingKind
from backend.domain.use_cases.market_hours import EASTERN_TIME

REST_URL = "http://thetaterminal.test"
WS_URL = "ws://thetaterminal.test/v1/events"


def _first_order_entry(
    strike: str,
    right: str,
    expiration: str = "2026-09-18",
    underlying_price: str = "769.36",
    root: str = "SPY",
) -> dict[str, object]:
    return {
        "contract": {"symbol": root, "expiration": expiration, "right": right, "strike": float(strike)},
        "data": [
            {
                "underlying_price": float(underlying_price),
                "delta": 0.5 if right == "CALL" else -0.5,
                "implied_vol": 0.16,
                "theta": -2.5,
                "vega": 8.2,
                "bid": 1.08,
                "ask": 1.09,
                "timestamp": "2026-08-31T14:35:22.752",
            }
        ],
    }


def _open_interest_entry(
    strike: str, right: str, oi: int, expiration: str = "2026-09-18", root: str = "SPY"
) -> dict[str, object]:
    return {
        "contract": {"symbol": root, "expiration": expiration, "right": right, "strike": float(strike)},
        "data": [{"open_interest": oi, "timestamp": "2026-08-31T06:30:00.000"}],
    }


def _daily_bars_response(
    count: int = 20, base_close: float = 769.0, daily_range: float = 2.0
) -> dict[str, object]:
    """Enough closed daily bars for a real (non-provisional) ATR — every
    near-the-money fetch now pulls this once per symbol per day to size
    its width. `daily_range` $ high/low range each day, flat close, gives
    ATR = daily_range (see tests/test_atr_range.py's own hand-verified
    flat-bar case) — pass 0 to get a degenerate zero-width ATR."""
    half_range = daily_range / 2
    rows = []
    for offset in range(count, 0, -1):
        day = date(2026, 8, 31) - timedelta(days=offset)
        rows.append(
            {
                "last_trade": f"{day.isoformat()}T16:00:00.000",
                "open": base_close - half_range / 2,
                "high": base_close + half_range,
                "low": base_close - half_range,
                "close": base_close,
            }
        )
    return {"response": rows}


def _make_client(handler: httpx.MockTransport | None, transport_handler=None) -> httpx.Client:
    transport = handler or httpx.MockTransport(transport_handler)
    return httpx.Client(base_url=REST_URL, transport=transport)


def _provider_with_transport(transport_handler) -> ThetaDataProvider:
    provider = ThetaDataProvider(REST_URL, WS_URL)
    provider._client = _make_client(None, transport_handler)
    provider._hub = ThetaStreamHub(WS_URL, provider._client)
    return provider


class TestHelpers:
    def test_build_occ_symbol_matches_mock_provider_pattern(self) -> None:
        occ = _build_occ_symbol("SPY", date(2026, 9, 18), ContractType.CALL, Decimal(770))
        assert occ == "SPY260918C00770000"

    def test_build_occ_symbol_put(self) -> None:
        occ = _build_occ_symbol("SPY", date(2026, 9, 18), ContractType.PUT, Decimal("769.5"))
        assert occ == "SPY260918P00769500"

    def test_parse_et_timestamp_attaches_eastern_time_not_utc(self) -> None:
        parsed = _parse_et_timestamp("2026-08-31T09:30:43.150")
        assert parsed.tzinfo == EASTERN_TIME
        assert parsed.hour == 9
        assert parsed.minute == 30

    def test_time_to_expiration_matches_hand_verified_real_case(self) -> None:
        # Real values from the 2026-08-31 investigation: SPY 766 call,
        # 0DTE, sampled at 14:35:29 ET -- T should be seconds-to-16:00/
        # 86400/365, not a whole-calendar-day count (which would be 0
        # for a same-day expiration).
        now_et = datetime(2026, 8, 31, 14, 35, 29, tzinfo=EASTERN_TIME)
        t = _time_to_expiration_years(date(2026, 8, 31), now_et)
        expected_seconds = (16 * 3600) - (14 * 3600 + 35 * 60 + 29)
        expected = Decimal(expected_seconds) / Decimal(86400) / Decimal(365)
        assert abs(t - expected) < Decimal("0.0000001")

    def test_time_to_expiration_is_zero_after_close(self) -> None:
        now_et = datetime(2026, 8, 31, 16, 30, 0, tzinfo=EASTERN_TIME)
        assert _time_to_expiration_years(date(2026, 8, 31), now_et) == Decimal(0)


class TestNearestExpirationCutoff:
    """Before MARKET_CLOSE_ET, today's own date must still be eligible as
    "nearest" (a genuine 0DTE). At/after MARKET_CLOSE_ET, today's 0DTE
    already has time_to_expiration<=0 (see
    test_time_to_expiration_is_zero_after_close) -- calculate_bsm_greeks
    zeroes gamma on every contract for it, so it can no longer stand in
    as "nearest" once the market's actually closed for the day (confirmed
    live, 2026-09 investigation)."""

    def test_before_close_todays_date_is_the_cutoff(self) -> None:
        now_et = datetime(2026, 9, 3, 15, 59, 59, tzinfo=EASTERN_TIME)
        assert _nearest_expiration_cutoff(now_et) == date(2026, 9, 3)

    def test_at_close_todays_date_is_already_excluded(self) -> None:
        now_et = datetime(2026, 9, 3, 16, 0, 0, tzinfo=EASTERN_TIME)
        assert _nearest_expiration_cutoff(now_et) == date(2026, 9, 4)

    def test_after_close_todays_date_is_excluded(self) -> None:
        now_et = datetime(2026, 9, 3, 16, 30, 0, tzinfo=EASTERN_TIME)
        assert _nearest_expiration_cutoff(now_et) == date(2026, 9, 4)


class TestGetOptionChain:
    def test_builds_contracts_with_bsm_gamma_vanna_charm_and_stream_volume(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if "greeks/first_order" in str(request.url):
                return httpx.Response(
                    200,
                    json={
                        "response": [
                            _first_order_entry("769.00", "CALL"),
                            _first_order_entry("769.00", "PUT"),
                        ]
                    },
                )
            if "open_interest" in str(request.url):
                return httpx.Response(
                    200,
                    json={
                        "response": [
                            _open_interest_entry("769.00", "CALL", 500),
                            _open_interest_entry("769.00", "PUT", 300),
                        ]
                    },
                )
            if "interest_rate/history/eod" in str(request.url):
                return httpx.Response(
                    200, json={"response": [{"rate": 3.64, "created": "2026-08-31"}]}
                )
            if "stock/history/eod" in str(request.url):
                return httpx.Response(200, json=_daily_bars_response())
            raise AssertionError(f"unexpected request: {request.url}")

        provider = _provider_with_transport(handler)
        occ_call = _build_occ_symbol("SPY", date(2026, 9, 18), ContractType.CALL, Decimal("769.00"))
        provider._hub.register_contract(
            occ_call, "SPY", date(2026, 9, 18), ContractType.CALL, Decimal("769.00")
        )
        provider._hub._cumulative_volume[occ_call] = 4242

        chain = provider.get_option_chain("SPY")

        assert chain.symbol == "SPY"
        assert chain.spot_price == Decimal("769.36")
        assert len(chain.contracts) == 2
        call = next(c for c in chain.contracts if c.contract_type == ContractType.CALL)
        put = next(c for c in chain.contracts if c.contract_type == ContractType.PUT)

        assert call.occ_symbol == occ_call
        assert call.volume == 4242
        assert put.volume == 0  # never registered/traded in this test
        assert call.last == (Decimal("1.08") + Decimal("1.09")) / 2
        assert call.open_interest == 500
        assert put.open_interest == 300
        assert call.iv == Decimal("0.16")
        assert call.greeks.delta == Decimal("0.5")
        # gamma/vanna/charm computed via BSM, not from ThetaData -- just
        # confirm they're real (non-zero) numbers, not left at zero.
        assert call.greeks.gamma > 0
        assert call.greeks.vanna != 0
        assert call.greeks.charm != 0
        # No dividend yield -- gamma/vanna/charm identical for call and put
        # at the same strike (confirmed property of the BSM formulas used).
        assert call.greeks.gamma == put.greeks.gamma
        assert call.greeks.vanna == put.greeks.vanna
        assert call.greeks.charm == put.greeks.charm

    def test_picks_the_nearest_of_several_returned_expirations(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if "greeks/first_order" in str(request.url):
                return httpx.Response(
                    200,
                    json={
                        "response": [
                            _first_order_entry("769.00", "CALL", expiration="2026-10-16"),
                            _first_order_entry("769.00", "CALL", expiration="2026-09-18"),
                            _first_order_entry("769.00", "CALL", expiration="2026-12-18"),
                        ]
                    },
                )
            if "open_interest" in str(request.url):
                return httpx.Response(200, json={"response": []})
            if "interest_rate/history/eod" in str(request.url):
                return httpx.Response(
                    200, json={"response": [{"rate": 3.64, "created": "2026-08-31"}]}
                )
            if "stock/history/eod" in str(request.url):
                return httpx.Response(200, json=_daily_bars_response())
            raise AssertionError(f"unexpected request: {request.url}")

        provider = _provider_with_transport(handler)
        chain = provider.get_option_chain("SPY")

        assert len(chain.contracts) == 1
        assert chain.contracts[0].expiration == date(2026, 9, 18)

    def test_raises_when_thetadata_returns_no_contracts(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"response": []})

        provider = _provider_with_transport(handler)
        with pytest.raises(RuntimeError):
            provider.get_option_chain("SPY")

    def test_raises_on_non_200_response(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, text="FREE subscription")

        provider = _provider_with_transport(handler)
        with pytest.raises(RuntimeError, match="403"):
            provider.get_option_chain("SPY")


class TestNearTheMoneyResubscription:
    """Confirmed live, 2026-09: the Trade/Quote Stream WebSocket
    subscriptions were only ever registered once, at
    ThetaDataProvider.start() (Worker startup) -- nothing re-discovered
    or widened them as spot drifted during the session. get_option_chain
    already recomputes "near-the-money right now" every scheduler cycle,
    so it's the natural place to also register a contract that's
    near-the-money now but wasn't at startup."""

    def _handler(self) -> object:
        def handler(request: httpx.Request) -> httpx.Response:
            if "greeks/first_order" in str(request.url):
                return httpx.Response(
                    200,
                    json={
                        "response": [
                            _first_order_entry("769.00", "CALL"),
                            _first_order_entry("769.00", "PUT"),
                        ]
                    },
                )
            if "open_interest" in str(request.url):
                return httpx.Response(200, json={"response": []})
            if "interest_rate/history/eod" in str(request.url):
                return httpx.Response(
                    200, json={"response": [{"rate": 3.64, "created": "2026-08-31"}]}
                )
            if "stock/history/eod" in str(request.url):
                return httpx.Response(200, json=_daily_bars_response())
            raise AssertionError(f"unexpected request: {request.url}")

        return handler

    def test_registers_a_newly_near_the_money_contract(self) -> None:
        provider = _provider_with_transport(self._handler())
        occ_call = _build_occ_symbol("SPY", date(2026, 9, 18), ContractType.CALL, Decimal("769.00"))
        occ_put = _build_occ_symbol("SPY", date(2026, 9, 18), ContractType.PUT, Decimal("769.00"))
        assert provider._hub.has_contract(occ_call) is False

        provider.get_option_chain("SPY")

        assert provider._hub.has_contract(occ_call) is True
        assert provider._hub.has_contract(occ_put) is True

    def test_requests_reconnect_when_a_contract_is_newly_registered(self) -> None:
        provider = _provider_with_transport(self._handler())
        reconnects = []
        provider._hub.request_reconnect = lambda: reconnects.append(1)

        provider.get_option_chain("SPY")

        assert reconnects == [1]

    def test_does_not_request_reconnect_when_nothing_new_is_registered(self) -> None:
        provider = _provider_with_transport(self._handler())
        occ_call = _build_occ_symbol("SPY", date(2026, 9, 18), ContractType.CALL, Decimal("769.00"))
        occ_put = _build_occ_symbol("SPY", date(2026, 9, 18), ContractType.PUT, Decimal("769.00"))
        for occ, contract_type in ((occ_call, ContractType.CALL), (occ_put, ContractType.PUT)):
            provider._hub.register_contract(
                occ, "SPY", date(2026, 9, 18), contract_type, Decimal("769.00")
            )
        reconnects = []
        provider._hub.request_reconnect = lambda: reconnects.append("reconnect")

        provider.get_option_chain("SPY")

        assert reconnects == []

    def test_price_drift_across_scheduler_cycles_widens_the_registered_set_without_dropping_the_old_one(
        self,
    ) -> None:
        """End-to-end simulation of the real scenario this exists for:
        market closed today, so this drives ThetaDataProvider.get_option_chain
        -- the real production code path, not a mock of the resubscription
        logic itself -- through two "scheduler cycles" with a mocked
        transport, the first at spot=769 and the second (simulating spot
        having drifted) at spot=800, confirming the newly-relevant strike
        gets registered and reconnected while the original one is *not*
        dropped (deliberately additive-only, see this class's own
        docstring and get_option_chain's inline comment for the
        keep-vs-unsubscribe trade-off)."""
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            if "greeks/first_order" in str(request.url):
                spot = "769.00" if call_count == 0 else "800.00"
                return httpx.Response(
                    200,
                    json={"response": [_first_order_entry(spot, "CALL", underlying_price=spot)]},
                )
            if "open_interest" in str(request.url):
                return httpx.Response(200, json={"response": []})
            if "interest_rate/history/eod" in str(request.url):
                return httpx.Response(
                    200, json={"response": [{"rate": 3.64, "created": "2026-08-31"}]}
                )
            if "stock/history/eod" in str(request.url):
                return httpx.Response(200, json=_daily_bars_response())
            raise AssertionError(f"unexpected request: {request.url}")

        provider = _provider_with_transport(handler)
        occ_at_769 = _build_occ_symbol("SPY", date(2026, 9, 18), ContractType.CALL, Decimal("769.00"))
        occ_at_800 = _build_occ_symbol("SPY", date(2026, 9, 18), ContractType.CALL, Decimal("800.00"))
        reconnects: list[int] = []
        provider._hub.request_reconnect = lambda: reconnects.append(len(reconnects) + 1)

        provider.get_option_chain("SPY")  # cycle 1: spot=769
        assert provider._hub.has_contract(occ_at_769) is True
        assert provider._hub.has_contract(occ_at_800) is False
        assert reconnects == [1]

        call_count = 1
        # NEAR_THE_MONEY_CACHE_TTL_SECONDS (10s) would otherwise serve
        # cycle 1's cached chain again -- cleared here to simulate "enough
        # real time passed for the cache to expire" without actually
        # sleeping 10s in the test.
        provider._near_the_money_cache.clear()
        provider.get_option_chain("SPY")  # cycle 2: spot drifted to 800

        assert provider._hub.has_contract(occ_at_800) is True, "new strike must be registered"
        assert provider._hub.has_contract(occ_at_769) is True, "old strike must not be dropped"
        assert reconnects == [1, 2], "second cycle must reconnect again for the newly-widened set"


class TestExpiredContractFiltering:
    """ThetaData's snapshot endpoint keeps returning an already-expired
    contract's last-known (dead) quote for a while after it expires --
    confirmed live (2026-09 investigation): a contract dated the
    previous day still came back with data (bid=0.0, implied_vol=6.19 =
    619%, last quote timestamped 16:15 the day it expired). Before this
    fix, `_fetch_near_the_money`'s "nearest expiration" selection
    (expiration=None) could pick that dead contract as "nearest" since
    it never excluded already-past dates. Fixture dates below are
    computed relative to the real clock (not a fixed literal) so these
    tests stay correct regardless of what day they're run."""

    def test_an_already_expired_contract_is_never_picked_as_nearest(self) -> None:
        yesterday = (datetime.now(EASTERN_TIME) - timedelta(days=1)).date().isoformat()
        future = (datetime.now(EASTERN_TIME) + timedelta(days=15)).date().isoformat()

        def handler(request: httpx.Request) -> httpx.Response:
            if "greeks/first_order" in str(request.url):
                return httpx.Response(
                    200,
                    json={
                        "response": [
                            # Dead contract -- expired, but ThetaData
                            # still returns it with obviously-stale data.
                            _first_order_entry("770.00", "CALL", expiration=yesterday),
                            _first_order_entry("769.00", "CALL", expiration=future),
                        ]
                    },
                )
            if "open_interest" in str(request.url):
                return httpx.Response(200, json={"response": []})
            if "interest_rate/history/eod" in str(request.url):
                return httpx.Response(200, json={"response": [{"rate": 3.64, "created": "2026-08-31"}]})
            if "stock/history/eod" in str(request.url):
                return httpx.Response(200, json=_daily_bars_response())
            raise AssertionError(f"unexpected request: {request.url}")

        provider = _provider_with_transport(handler)
        chain = provider.get_option_chain("SPY")  # no expiration -> nearest

        assert chain.contracts[0].expiration == date.fromisoformat(future)

    def test_a_genuine_0dte_expiration_is_not_excluded(self) -> None:
        """The filter must exclude anything before the current cutoff
        (see TestNearestExpirationCutoff), not the cutoff date itself --
        a real 0DTE still inside the cutoff must still win. Uses the real
        cutoff (not a hardcoded "today") so this integration test stays
        correct no matter what time of day the suite runs -- including
        after MARKET_CLOSE_ET, when the cutoff has already rolled to
        tomorrow."""
        cutoff = _nearest_expiration_cutoff(datetime.now(EASTERN_TIME)).isoformat()

        def handler(request: httpx.Request) -> httpx.Response:
            if "greeks/first_order" in str(request.url):
                return httpx.Response(
                    200,
                    json={"response": [_first_order_entry("769.00", "CALL", expiration=cutoff)]},
                )
            if "open_interest" in str(request.url):
                return httpx.Response(200, json={"response": []})
            if "interest_rate/history/eod" in str(request.url):
                return httpx.Response(200, json={"response": [{"rate": 3.64, "created": "2026-08-31"}]})
            if "stock/history/eod" in str(request.url):
                return httpx.Response(200, json=_daily_bars_response())
            raise AssertionError(f"unexpected request: {request.url}")

        provider = _provider_with_transport(handler)
        chain = provider.get_option_chain("SPY")

        assert chain.contracts[0].expiration == date.fromisoformat(cutoff)

    def test_raises_when_every_available_expiration_is_already_expired(self) -> None:
        yesterday = (datetime.now(EASTERN_TIME) - timedelta(days=1)).date().isoformat()

        def handler(request: httpx.Request) -> httpx.Response:
            if "greeks/first_order" in str(request.url):
                return httpx.Response(
                    200,
                    json={"response": [_first_order_entry("769.00", "CALL", expiration=yesterday)]},
                )
            raise AssertionError(f"unexpected request: {request.url}")

        provider = _provider_with_transport(handler)
        with pytest.raises(RuntimeError, match="unexpired"):
            provider.get_option_chain("SPY")

    def test_an_explicit_expiration_request_is_not_filtered(self) -> None:
        """Scope check -- this fix is only about the "nearest" (no
        expiration given) discovery path. A caller that explicitly asks
        for a specific (even past) date is untouched -- that's a
        different, deliberately out-of-scope question (e.g. a
        historical drill-down), not what was reported or asked here."""
        yesterday_date = datetime.now(EASTERN_TIME).date() - timedelta(days=1)
        yesterday = yesterday_date.isoformat()

        def handler(request: httpx.Request) -> httpx.Response:
            if "greeks/first_order" in str(request.url):
                return httpx.Response(
                    200,
                    json={"response": [_first_order_entry("769.00", "CALL", expiration=yesterday)]},
                )
            if "open_interest" in str(request.url):
                return httpx.Response(200, json={"response": []})
            if "interest_rate/history/eod" in str(request.url):
                return httpx.Response(200, json={"response": [{"rate": 3.64, "created": "2026-08-31"}]})
            if "stock/history/eod" in str(request.url):
                return httpx.Response(200, json=_daily_bars_response())
            raise AssertionError(f"unexpected request: {request.url}")

        provider = _provider_with_transport(handler)
        chain = provider.get_option_chain("SPY", expiration=yesterday_date)

        assert chain.contracts[0].expiration == yesterday_date


class TestWeeklyRootCombination:
    """SPX/NDX split their real open interest across two independently-
    traded roots (SPX/SPXW, NDX/NDXP) -- confirmed live (2026-09
    investigation) that even on a shared/overlapping expiration, the
    same strike/right's bid/ask genuinely differs between the two roots,
    so they must both be queried and combined, never deduplicated by
    (strike, expiration, right) alone. See _roots_for_symbol's own
    docstring in provider.py for the full investigation writeup."""

    # Fixture data, keyed by (root, expiration) -- SPX's own root only
    # lists the 09-18 monthly; SPXW additionally lists a genuinely
    # nearer 09-03 expiration, and also lists 09-18 itself (real,
    # confirmed overlap -- see this class's own docstring) with
    # different bid/ask than SPX's 09-18 entry for the same strike.
    _GREEKS_BY_ROOT_EXPIRATION: ClassVar[dict[tuple[str, str], list[dict[str, object]]]] = {
        ("SPX", "2026-09-18"): [_first_order_entry("7700", "CALL", expiration="2026-09-18", root="SPX")],
        ("SPXW", "2026-09-18"): [
            {
                **_first_order_entry("7700", "CALL", expiration="2026-09-18", root="SPXW"),
                "data": [
                    {
                        "underlying_price": 7700.0,
                        "delta": 0.55,
                        "implied_vol": 0.15,
                        "theta": -3.1,
                        "vega": 9.0,
                        "bid": 495.0,
                        "ask": 505.0,
                        "timestamp": "2026-09-03T13:46:42.253",
                    }
                ],
            },
        ],
        ("SPXW", "2026-09-03"): [
            _first_order_entry(
                "7710", "PUT", expiration="2026-09-03", underlying_price="7700.0", root="SPXW"
            ),
        ],
    }
    _OI_BY_ROOT_EXPIRATION: ClassVar[dict[tuple[str, str], list[dict[str, object]]]] = {
        ("SPX", "2026-09-18"): [_open_interest_entry("7700", "CALL", 700, root="SPX")],
        ("SPXW", "2026-09-18"): [_open_interest_entry("7700", "CALL", 250, root="SPXW")],
        ("SPXW", "2026-09-03"): [_open_interest_entry("7710", "PUT", 90, root="SPXW")],
    }

    def _spx_handler(self, request: httpx.Request) -> httpx.Response:
        params = request.url.params
        symbol = params.get("symbol")
        expiration_param = params.get("expiration")
        path = request.url.path
        if path == "/v3/option/snapshot/greeks/first_order":
            if expiration_param == "*":
                # ThetaData's own "all expirations" mode -- return every
                # fixture entry for this root, across every expiration.
                entries = [
                    entry
                    for (root, _exp), rows in self._GREEKS_BY_ROOT_EXPIRATION.items()
                    if root == symbol
                    for entry in rows
                ]
            else:
                entries = self._GREEKS_BY_ROOT_EXPIRATION.get((symbol, expiration_param), [])
            return httpx.Response(200, json={"response": entries})
        if path == "/v3/option/snapshot/open_interest":
            entries = self._OI_BY_ROOT_EXPIRATION.get((symbol, expiration_param), [])
            return httpx.Response(200, json={"response": entries})
        if path == "/v3/interest_rate/history/eod":
            return httpx.Response(200, json={"response": [{"rate": 3.64, "created": "2026-08-31"}]})
        if path == "/v3/index/history/eod":
            return httpx.Response(200, json=_daily_bars_response(base_close=7700.0, daily_range=50.0))
        raise AssertionError(f"unexpected request: {request.url}")

    def test_get_option_chain_queries_both_roots_and_combines_contracts(self) -> None:
        provider = _provider_with_transport(self._spx_handler)

        chain = provider.get_option_chain("SPX", expiration=date(2026, 9, 18))

        assert len(chain.contracts) == 2
        occ_symbols = {c.occ_symbol for c in chain.contracts}
        # Distinct OCC symbols per root -- proves the two same-strike
        # contracts were kept separate, not collapsed into one.
        assert _build_occ_symbol("SPX", date(2026, 9, 18), ContractType.CALL, Decimal(7700)) in occ_symbols
        assert _build_occ_symbol("SPXW", date(2026, 9, 18), ContractType.CALL, Decimal(7700)) in occ_symbols

    def test_open_interest_does_not_collide_between_roots_on_the_same_strike(self) -> None:
        provider = _provider_with_transport(self._spx_handler)

        chain = provider.get_option_chain("SPX", expiration=date(2026, 9, 18))

        spx_call = next(
            c
            for c in chain.contracts
            if c.occ_symbol == _build_occ_symbol("SPX", date(2026, 9, 18), ContractType.CALL, Decimal(7700))
        )
        spxw_call = next(
            c
            for c in chain.contracts
            if c.occ_symbol == _build_occ_symbol("SPXW", date(2026, 9, 18), ContractType.CALL, Decimal(7700))
        )
        # Each root's own OI survives intact -- a (strike, right)-only key
        # would have let one silently overwrite the other.
        assert spx_call.open_interest == 700
        assert spxw_call.open_interest == 250
        # And each root's own bid/ask survives too -- proves they weren't
        # merged/averaged into a single contract.
        assert spx_call.bid == Decimal("1.08")
        assert spxw_call.bid == Decimal("495.0")

    def test_nearest_expiration_considers_both_roots_not_just_the_bare_root(self) -> None:
        # SPX's own root here only has the 09-18 monthly; SPXW has a
        # genuinely nearer expiration -- the combined "nearest" must be
        # that nearer date, not 09-18, exactly the live-confirmed bug.
        # Uses the real cutoff (see TestNearestExpirationCutoff) instead
        # of a hardcoded near date, in a handler local to this test, so
        # it stays correct at any time of day, including after
        # MARKET_CLOSE_ET when the shared class fixture's own hardcoded
        # "2026-09-03" would otherwise be wrongly excluded.
        nearer = _nearest_expiration_cutoff(datetime.now(EASTERN_TIME)).isoformat()
        greeks_by_root_expiration = {
            ("SPX", "2026-09-18"): [_first_order_entry("7700", "CALL", expiration="2026-09-18", root="SPX")],
            (
                "SPXW",
                nearer,
            ): [_first_order_entry("7710", "PUT", expiration=nearer, underlying_price="7700.0", root="SPXW")],
        }
        oi_by_root_expiration = {
            ("SPX", "2026-09-18"): [_open_interest_entry("7700", "CALL", 700, root="SPX")],
            ("SPXW", nearer): [_open_interest_entry("7710", "PUT", 90, expiration=nearer, root="SPXW")],
        }

        def handler(request: httpx.Request) -> httpx.Response:
            params = request.url.params
            symbol = params.get("symbol")
            expiration_param = params.get("expiration")
            path = request.url.path
            if path == "/v3/option/snapshot/greeks/first_order":
                if expiration_param == "*":
                    entries = [
                        entry
                        for (root, _exp), rows in greeks_by_root_expiration.items()
                        if root == symbol
                        for entry in rows
                    ]
                else:
                    entries = greeks_by_root_expiration.get((symbol, expiration_param), [])
                return httpx.Response(200, json={"response": entries})
            if path == "/v3/option/snapshot/open_interest":
                entries = oi_by_root_expiration.get((symbol, expiration_param), [])
                return httpx.Response(200, json={"response": entries})
            if path == "/v3/interest_rate/history/eod":
                return httpx.Response(200, json={"response": [{"rate": 3.64, "created": "2026-08-31"}]})
            if path == "/v3/index/history/eod":
                return httpx.Response(200, json=_daily_bars_response(base_close=7700.0, daily_range=50.0))
            raise AssertionError(f"unexpected request: {request.url}")

        provider = _provider_with_transport(handler)

        chain = provider.get_option_chain("SPX")  # no expiration -> nearest

        assert chain.contracts[0].expiration == date.fromisoformat(nearer)
        assert chain.contracts[0].occ_symbol.startswith("SPXW")

    def test_one_roots_472_for_a_date_it_lacks_does_not_abort_the_combined_fetch(self) -> None:
        """Confirmed live: querying a specific expiration for a root that
        simply doesn't list that date returns HTTP 472 ("no data found"),
        not an empty 200 -- e.g. SPX's own root has no 2026-09-03 listing
        at all, only SPXW does. Reproduced the real live failure this
        caused (get_option_chain("SPX") raising RuntimeError from the
        SPX-root open-interest call alone, even though SPXW's own
        open-interest call for that same date succeeded) before adding
        _get_json_allow_no_data to fix it."""
        expiration = date(2026, 9, 3)

        def handler(request: httpx.Request) -> httpx.Response:
            params = request.url.params
            symbol = params.get("symbol")
            path = request.url.path
            if path == "/v3/option/snapshot/greeks/first_order":
                if symbol == "SPX":
                    return httpx.Response(472, text="No data found for your request")
                return httpx.Response(
                    200,
                    json={
                        "response": [
                            _first_order_entry(
                                "7700", "CALL", expiration="2026-09-03", root="SPXW"
                            )
                        ]
                    },
                )
            if path == "/v3/option/snapshot/open_interest":
                if symbol == "SPX":
                    return httpx.Response(472, text="No data found for your request")
                return httpx.Response(
                    200, json={"response": [_open_interest_entry("7700", "CALL", 250, root="SPXW")]}
                )
            if path == "/v3/interest_rate/history/eod":
                return httpx.Response(200, json={"response": [{"rate": 3.64, "created": "2026-08-31"}]})
            if path == "/v3/index/history/eod":
                return httpx.Response(200, json=_daily_bars_response(base_close=7700.0, daily_range=50.0))
            raise AssertionError(f"unexpected request: {request.url}")

        provider = _provider_with_transport(handler)

        chain = provider.get_option_chain("SPX", expiration=expiration)

        assert len(chain.contracts) == 1
        assert chain.contracts[0].occ_symbol.startswith("SPXW")
        assert chain.contracts[0].open_interest == 250

    def test_a_genuine_error_from_one_root_still_propagates(self) -> None:
        """_get_json_allow_no_data only swallows 472 -- a real failure
        (auth, 5xx, ...) on either root must still raise, not be silently
        treated as "that root has nothing"."""

        def handler(request: httpx.Request) -> httpx.Response:
            if "greeks/first_order" in str(request.url):
                return httpx.Response(500, text="internal server error")
            raise AssertionError(f"unexpected request: {request.url}")

        provider = _provider_with_transport(handler)

        with pytest.raises(RuntimeError, match="500"):
            provider.get_option_chain("SPX", expiration=date(2026, 9, 18))

    def test_equity_symbols_are_not_combined_with_a_second_root(self) -> None:
        """Scope check -- SPY (and every other non-SPX/NDX symbol) must
        keep making exactly one greeks/first_order request, not two."""
        request_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal request_count
            if "greeks/first_order" in str(request.url):
                request_count += 1
                return httpx.Response(200, json={"response": [_first_order_entry("769.00", "CALL")]})
            if "open_interest" in str(request.url):
                return httpx.Response(200, json={"response": []})
            if "interest_rate/history/eod" in str(request.url):
                return httpx.Response(200, json={"response": [{"rate": 3.64, "created": "2026-08-31"}]})
            if "stock/history/eod" in str(request.url):
                return httpx.Response(200, json=_daily_bars_response())
            raise AssertionError(f"unexpected request: {request.url}")

        provider = _provider_with_transport(handler)
        provider.get_option_chain("SPY")

        assert request_count == 1

    def test_vix_combines_with_its_weekly_root(self) -> None:
        """VIX/VIXW: same combine-don't-dedupe mechanism as SPX/SPXW and
        NDX/NDXP above, added after confirming live (2026-09) that VIX
        and VIXW's near-term expirations never actually overlap -- VIX
        only lists the standard monthlies, VIXW's own listing skips
        those same dates. See _WEEKLY_ROOT_BY_SYMBOL's own comment in
        provider.py for the full investigation writeup. Mirrors
        test_nearest_expiration_considers_both_roots_not_just_the_bare_root
        above, for VIX/VIXW instead of SPX/SPXW."""
        assert _roots_for_symbol("VIX") == ("VIX", "VIXW")

        nearer = _nearest_expiration_cutoff(datetime.now(EASTERN_TIME)).isoformat()
        greeks_by_root_expiration = {
            ("VIX", "2026-10-21"): [_first_order_entry("14.00", "CALL", expiration="2026-10-21", root="VIX")],
            (
                "VIXW",
                nearer,
            ): [_first_order_entry("14.00", "CALL", expiration=nearer, underlying_price="14.06", root="VIXW")],
        }
        oi_by_root_expiration = {
            ("VIX", "2026-10-21"): [_open_interest_entry("14.00", "CALL", 700, expiration="2026-10-21", root="VIX")],
            ("VIXW", nearer): [_open_interest_entry("14.00", "CALL", 90, expiration=nearer, root="VIXW")],
        }

        def handler(request: httpx.Request) -> httpx.Response:
            params = request.url.params
            symbol = params.get("symbol")
            expiration_param = params.get("expiration")
            path = request.url.path
            if path == "/v3/option/snapshot/greeks/first_order":
                if expiration_param == "*":
                    entries = [
                        entry
                        for (root, _exp), rows in greeks_by_root_expiration.items()
                        if root == symbol
                        for entry in rows
                    ]
                else:
                    entries = greeks_by_root_expiration.get((symbol, expiration_param), [])
                return httpx.Response(200, json={"response": entries})
            if path == "/v3/option/snapshot/open_interest":
                entries = oi_by_root_expiration.get((symbol, expiration_param), [])
                return httpx.Response(200, json={"response": entries})
            if path == "/v3/interest_rate/history/eod":
                return httpx.Response(200, json={"response": [{"rate": 3.64, "created": "2026-08-31"}]})
            if path == "/v3/index/history/eod":
                return httpx.Response(200, json=_daily_bars_response(base_close=14.0, daily_range=1.0))
            raise AssertionError(f"unexpected request: {request.url}")

        provider = _provider_with_transport(handler)

        chain = provider.get_option_chain("VIX")  # no expiration -> nearest

        # VIXW's near-term expiration is nearer than VIX's own 10-21
        # monthly -- combining both roots is what makes it reachable at
        # all, matching what the user asked to confirm.
        assert chain.contracts[0].expiration == date.fromisoformat(nearer)
        assert chain.contracts[0].occ_symbol.startswith("VIXW")


class TestGetUnderlyingSnapshot:
    def test_approximates_atm_iv_and_pc_oi_ratio_from_near_the_money_chain(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if "greeks/first_order" in str(request.url):
                return httpx.Response(
                    200,
                    json={
                        "response": [
                            _first_order_entry("768.00", "CALL"),
                            _first_order_entry("769.00", "CALL"),
                        ]
                    },
                )
            if "open_interest" in str(request.url):
                return httpx.Response(
                    200,
                    json={
                        "response": [
                            _open_interest_entry("768.00", "CALL", 400),
                            _open_interest_entry("769.00", "CALL", 600),
                        ]
                    },
                )
            if "stock/history/eod" in str(request.url):
                return httpx.Response(200, json=_daily_bars_response())
            if "stock/snapshot/ohlc" in str(request.url):
                return httpx.Response(200, json={"response": [{"volume": 16396508}]})
            raise AssertionError(f"unexpected request: {request.url}")

        provider = _provider_with_transport(handler)
        snapshot = provider.get_underlying_snapshot("SPY")

        assert snapshot.symbol == "SPY"
        assert snapshot.price == Decimal("769.36")
        # GET /v3/stock/snapshot/ohlc closes the gap that used to leave
        # this hardcoded at 0 -- confirmed live against the real value
        # for SPY (see _fetch_underlying_volume's own docstring).
        assert snapshot.volume == 16396508
        assert snapshot.atm_iv == Decimal("0.16")
        # All open interest sampled above is CALL-side (no puts in this
        # response) -> put_oi is 0 -> documented pc_oi_ratio fallback.
        assert snapshot.pc_oi_ratio == Decimal(0)
        # No 25-delta strikes fetched -> documented as 0, not guessed.
        assert snapshot.skew_25d == Decimal(0)


class TestFetchUnderlyingVolume:
    def test_routes_equities_to_stock_snapshot_ohlc(self) -> None:
        seen_paths = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen_paths.append(request.url.path)
            return httpx.Response(200, json={"response": [{"volume": 16396508}]})

        provider = _provider_with_transport(handler)
        volume = provider._fetch_underlying_volume("SPY", UnderlyingKind.EQUITY)

        assert seen_paths == ["/v3/stock/snapshot/ohlc"]
        assert volume == 16396508

    def test_routes_indices_to_index_snapshot_ohlc(self) -> None:
        seen_paths = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen_paths.append(request.url.path)
            # Confirmed live: an index's own OHLC snapshot volume is
            # always 0 -- it has no share volume of its own, only its
            # component stocks do.
            return httpx.Response(200, json={"response": [{"volume": 0}]})

        provider = _provider_with_transport(handler)
        volume = provider._fetch_underlying_volume("SPX", UnderlyingKind.INDEX)

        assert seen_paths == ["/v3/index/snapshot/ohlc"]
        assert volume == 0

    def test_futures_return_zero_without_any_request(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("no request should be made for a future underlying")

        provider = _provider_with_transport(handler)
        assert provider._fetch_underlying_volume("ES", UnderlyingKind.FUTURE) == 0

    def test_falls_back_to_zero_without_raising_on_a_failed_request(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # 472 is ThetaData's own custom status code for "no data found"
        # (confirmed live, 2026-09 investigation) -- _get_json raises
        # RuntimeError on any non-200, which must not escape here and
        # fail the whole snapshot refresh cycle over one field.
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(472, text="No data found for your request")

        provider = _provider_with_transport(handler)

        with caplog.at_level(logging.ERROR):
            volume = provider._fetch_underlying_volume("SPY", UnderlyingKind.EQUITY)

        assert volume == 0
        assert any("SPY" in record.message for record in caplog.records)

    def test_falls_back_to_zero_when_response_has_no_rows(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"response": []})

        provider = _provider_with_transport(handler)
        assert provider._fetch_underlying_volume("SPY", UnderlyingKind.EQUITY) == 0


class TestGetDailyBars:
    def test_routes_equities_to_stock_history_eod(self) -> None:
        seen_paths = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen_paths.append(request.url.path)
            return httpx.Response(
                200,
                json={
                    "response": [
                        {
                            "last_trade": "2026-08-28T17:15:24.342",
                            "open": 771.84,
                            "high": 775.30,
                            "low": 768.31,
                            "close": 769.35,
                        }
                    ]
                },
            )

        provider = _provider_with_transport(handler)
        bars = provider.get_daily_bars("SPY", days=5)

        assert seen_paths == ["/v3/stock/history/eod"]
        assert len(bars) == 1
        assert bars[0].symbol == "SPY"
        assert bars[0].date == date(2026, 8, 28)
        assert bars[0].close == Decimal("769.35")

    def test_routes_indices_to_index_history_eod(self) -> None:
        seen_paths = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen_paths.append(request.url.path)
            return httpx.Response(
                200,
                json={
                    "response": [
                        {
                            "last_trade": "2026-08-28T16:04:41.000",
                            "open": 7735.17,
                            "high": 7771.48,
                            "low": 7700.91,
                            "close": 7711.76,
                        }
                    ]
                },
            )

        provider = _provider_with_transport(handler)
        bars = provider.get_daily_bars("SPX", days=5)

        assert seen_paths == ["/v3/index/history/eod"]
        assert bars[0].symbol == "SPX"

    def test_futures_return_empty_list_without_any_request(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("no request should be made for a future underlying")

        provider = _provider_with_transport(handler)
        assert provider.get_daily_bars("ES", days=5) == []


class TestDailyBarsCaching:
    """get_daily_bars() had no cache at all before this -- confirmed live,
    2026-09: every 30s scheduler cycle re-fetched it from ThetaData for
    all 15 active symbols even though an EOD bar can't change again
    until the next session closes, a real contributor to the threadpool
    contention that made /gamma and /market queue behind the scheduler."""

    def test_second_call_within_ttl_reuses_the_cached_bars(self) -> None:
        request_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal request_count
            if "stock/history/eod" in str(request.url):
                request_count += 1
                return httpx.Response(200, json=_daily_bars_response())
            raise AssertionError(f"unexpected request: {request.url}")

        provider = _provider_with_transport(handler)
        first = provider.get_daily_bars("SPY", days=5)
        second = provider.get_daily_bars("SPY", days=5)

        assert request_count == 1
        assert second == first

    def test_different_days_argument_is_not_served_from_the_others_cache(self) -> None:
        request_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal request_count
            if "stock/history/eod" in str(request.url):
                request_count += 1
                return httpx.Response(200, json=_daily_bars_response())
            raise AssertionError(f"unexpected request: {request.url}")

        provider = _provider_with_transport(handler)
        provider.get_daily_bars("SPY", days=5)
        provider.get_daily_bars("SPY", days=20)

        assert request_count == 2

    def test_expired_cache_re_fetches(self) -> None:
        request_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal request_count
            if "stock/history/eod" in str(request.url):
                request_count += 1
                return httpx.Response(200, json=_daily_bars_response())
            raise AssertionError(f"unexpected request: {request.url}")

        provider = _provider_with_transport(handler)
        provider.get_daily_bars("SPY", days=5)
        provider._daily_bars_cache[("SPY", 5)] = (
            provider._daily_bars_cache[("SPY", 5)][0] - DAILY_BARS_CACHE_TTL_SECONDS - 1,
            provider._daily_bars_cache[("SPY", 5)][1],
        )
        provider.get_daily_bars("SPY", days=5)

        assert request_count == 2


def _year_holidays_response() -> dict[str, object]:
    # A real subset confirmed live, 2026-09-11, against
    # /v3/calendar/year_holidays?year=2026&format=json.
    return {
        "response": [
            {"date": "2026-01-01", "type": "full_close", "open": None, "close": None},
            {"date": "2026-11-26", "type": "full_close", "open": None, "close": None},
            {"date": "2026-11-27", "type": "early_close", "open": "09:30:00", "close": "13:00:00"},
            {"date": "2026-12-25", "type": "full_close", "open": None, "close": None},
        ]
    }


class TestGetMarketHolidays:
    def test_parses_full_close_and_early_close_rows(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/v3/calendar/year_holidays"
            assert request.url.params["year"] == "2026"
            return httpx.Response(200, json=_year_holidays_response())

        provider = _provider_with_transport(handler)
        holidays = provider.get_market_holidays(2026)

        assert len(holidays) == 4
        new_years = next(h for h in holidays if h.date == date(2026, 1, 1))
        assert new_years.closure_type is MarketHolidayType.FULL_CLOSE
        assert new_years.open is None
        assert new_years.close is None

        day_after_thanksgiving = next(h for h in holidays if h.date == date(2026, 11, 27))
        assert day_after_thanksgiving.closure_type is MarketHolidayType.EARLY_CLOSE
        assert day_after_thanksgiving.open == dtime(9, 30)
        assert day_after_thanksgiving.close == dtime(13, 0)


class TestMarketHolidaysCaching:
    """Same reasoning as TestDailyBarsCaching -- a published year's
    holidays don't change intra-year in practice, so re-fetching on every
    scheduler tick (every 30s) would be pure waste."""

    def test_second_call_within_ttl_reuses_the_cached_holidays(self) -> None:
        request_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal request_count
            request_count += 1
            return httpx.Response(200, json=_year_holidays_response())

        provider = _provider_with_transport(handler)
        first = provider.get_market_holidays(2026)
        second = provider.get_market_holidays(2026)

        assert request_count == 1
        assert second == first

    def test_different_year_is_not_served_from_the_others_cache(self) -> None:
        request_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal request_count
            request_count += 1
            return httpx.Response(200, json=_year_holidays_response())

        provider = _provider_with_transport(handler)
        provider.get_market_holidays(2026)
        provider.get_market_holidays(2027)

        assert request_count == 2

    def test_expired_cache_re_fetches(self) -> None:
        request_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal request_count
            request_count += 1
            return httpx.Response(200, json=_year_holidays_response())

        provider = _provider_with_transport(handler)
        provider.get_market_holidays(2026)
        provider._market_holidays_cache[2026] = (
            provider._market_holidays_cache[2026][0] - MARKET_HOLIDAYS_CACHE_TTL_SECONDS - 1,
            provider._market_holidays_cache[2026][1],
        )
        provider.get_market_holidays(2026)

        assert request_count == 2


class TestGetMinuteBars:
    """get_minute_bars: the Indices Pro historical backfill's only data
    source (backend/scripts/backfill_minute_history.py) -- not part of
    IDataProvider, index-only, confirmed live against the real
    /v3/index/history/ohlc endpoint before this test was written (see
    that script's own docstring)."""

    def test_routes_to_index_history_ohlc_with_1m_interval(self) -> None:
        seen_paths = []
        seen_params = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen_paths.append(request.url.path)
            seen_params.append(dict(request.url.params))
            return httpx.Response(
                200,
                json={
                    "response": [
                        {
                            "timestamp": "2026-09-03T09:30:00.000",
                            "open": 7686.71,
                            "high": 7702.62,
                            "low": 7686.71,
                            "close": 7702.06,
                            "volume": 0,
                            "count": 0,
                            "vwap": 0.0,
                        },
                        {
                            "timestamp": "2026-09-03T09:31:00.000",
                            "open": 7701.82,
                            "high": 7702.59,
                            "low": 7698.18,
                            "close": 7701.22,
                            "volume": 0,
                            "count": 0,
                            "vwap": 0.0,
                        },
                    ]
                },
            )

        provider = _provider_with_transport(handler)
        bars = provider.get_minute_bars("SPX", date(2026, 9, 3), date(2026, 9, 3))

        assert seen_paths == ["/v3/index/history/ohlc"]
        assert seen_params[0]["interval"] == "1m"
        assert seen_params[0]["start_date"] == "20260903"
        assert seen_params[0]["end_date"] == "20260903"
        assert len(bars) == 2
        assert bars[0].symbol == "SPX"
        assert bars[0].time == datetime(2026, 9, 3, 9, 30, tzinfo=EASTERN_TIME)
        assert bars[0].close == Decimal("7702.06")
        assert bars[0].volume == 0

    def test_rejects_non_index_underlyings(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("no request should be made for a non-index underlying")

        provider = _provider_with_transport(handler)

        with pytest.raises(RuntimeError, match="index"):
            provider.get_minute_bars("SPY", date(2026, 9, 3), date(2026, 9, 3))


class TestRiskFreeRateCaching:
    def test_caches_the_rate_per_day_without_a_second_request(self) -> None:
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(
                200, json={"response": [{"rate": 3.64, "created": "2026-08-31"}]}
            )

        provider = _provider_with_transport(handler)
        first = provider._risk_free_rate()
        second = provider._risk_free_rate()

        assert first == second == Decimal("0.0364")
        assert call_count == 1

    def test_raises_when_no_rate_rows_are_returned(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"response": []})

        provider = _provider_with_transport(handler)
        with pytest.raises(RuntimeError):
            provider._risk_free_rate()


class TestReqResponseHandling:
    """Simulated only — a real rejection was never observed (see
    _log_req_response's own docstring), forcing one would mean risking
    the shared, already-running Theta Terminal. Exact message shape
    confirmed live (2026-09 investigation): {"header": {"type":
    "REQ_RESPONSE", "status": "CONNECTED", "response": "SUBSCRIBED",
    "req_id": N}}."""

    def test_accepted_subscription_logs_at_debug_not_error(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        message = {
            "header": {
                "type": "REQ_RESPONSE",
                "status": "CONNECTED",
                "response": "SUBSCRIBED",
                "req_id": 7,
            }
        }

        with caplog.at_level(logging.DEBUG):
            _log_req_response("ThetaTradeStream", message)

        assert any(
            record.levelno == logging.DEBUG and "7" in record.message
            for record in caplog.records
        )
        assert not any(record.levelno == logging.ERROR for record in caplog.records)

    def test_rejected_subscription_logs_visibly_as_an_error(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        message = {
            "header": {
                "type": "REQ_RESPONSE",
                "status": "CONNECTED",
                "response": "SYMBOL_NOT_FOUND",
                "req_id": 12,
            }
        }

        with caplog.at_level(logging.DEBUG):
            _log_req_response("ThetaQuoteStream", message)

        errors = [record for record in caplog.records if record.levelno == logging.ERROR]
        assert len(errors) == 1
        assert "ThetaQuoteStream" in errors[0].message
        assert "SYMBOL_NOT_FOUND" in errors[0].message
        assert "12" in errors[0].message

    def test_missing_response_field_is_treated_as_a_rejection_not_ignored(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Anything other than the literal "SUBSCRIBED" string must be
        # visible, per the user's own instruction -- including malformed
        # or unexpected messages, not just a known rejection reason.
        message = {"header": {"type": "REQ_RESPONSE", "status": "CONNECTED", "req_id": 3}}

        with caplog.at_level(logging.DEBUG):
            _log_req_response("ThetaUnderlyingTradeStream", message)

        assert any(record.levelno == logging.ERROR for record in caplog.records)

    def test_consume_routes_req_response_through_the_shared_helper(self) -> None:
        """Confirms the elif branch exists in ThetaStreamHub's own
        message-loop method (not just that _log_req_response itself
        works) -- reads the compiled source directly rather than driving
        a full websocket loop, matching this file's own convention of
        testing _handle_option_trade/_handle_quote/_handle_underlying_trade
        directly instead of the recv() loop around them. All 3 logical
        streams share this one loop now (see ThetaStreamHub's own
        docstring for why: ThetaData's docs only support one connection
        to this endpoint)."""
        import inspect

        source = inspect.getsource(ThetaStreamHub._consume)
        assert '"REQ_RESPONSE"' in source
        assert "_log_req_response" in source


class TestOptionTradeHandling:
    def test_handle_option_trade_accumulates_cumulative_volume(self) -> None:
        stream = ThetaStreamHub(WS_URL, httpx.Client(base_url=REST_URL))
        message = {
            "header": {"type": "TRADE", "status": "CONNECTED"},
            "contract": {
                "security_type": "OPTION",
                "root": "SPY",
                "expiration": 20260918,
                "strike": 770000,
                "right": "C",
            },
            "trade": {"size": 15, "price": 1.09, "sequence": 12345},
        }

        stream._handle_option_trade(message)
        stream._handle_option_trade(message)

        occ = _build_occ_symbol("SPY", date(2026, 9, 18), ContractType.CALL, Decimal(770))
        assert stream.cumulative_volume(occ) == 30

    @pytest.mark.asyncio
    async def test_handle_option_trade_publishes_a_flow_event_to_subscribers(self) -> None:
        stream = ThetaStreamHub(WS_URL, httpx.Client(base_url=REST_URL))
        queue = stream.subscribe_trade_queue("SPY")
        message = {
            "header": {"type": "TRADE", "status": "CONNECTED"},
            "contract": {
                "security_type": "OPTION",
                "root": "SPY",
                "expiration": 20260918,
                "strike": 770000,
                "right": "C",
            },
            "trade": {"size": 10, "price": 2.00, "sequence": 1},
        }

        stream._handle_option_trade(message)
        event = await asyncio.wait_for(queue.get(), timeout=1)

        assert event.symbol == "SPY"
        assert event.size == 10
        assert event.premium == Decimal("2.00") * Decimal(10) * Decimal(100)

    def test_handle_option_trade_ignores_incomplete_messages(self) -> None:
        stream = ThetaStreamHub(WS_URL, httpx.Client(base_url=REST_URL))
        stream._handle_option_trade({"contract": {"root": "SPY"}, "trade": {}})
        # No exception, no volume recorded anywhere.
        assert stream._cumulative_volume == {}

    def test_has_contract_reflects_registration(self) -> None:
        stream = ThetaStreamHub(WS_URL, httpx.Client(base_url=REST_URL))
        occ = _build_occ_symbol("SPY", date(2026, 9, 18), ContractType.CALL, Decimal(770))

        assert stream.has_contract(occ) is False

        stream.register_contract(occ, "SPY", date(2026, 9, 18), ContractType.CALL, Decimal(770))

        assert stream.has_contract(occ) is True

    def test_subscribe_option_sends_the_documented_trade_payload(self) -> None:
        stream = ThetaStreamHub(WS_URL, httpx.Client(base_url=REST_URL))
        sent: list[str] = []

        class _FakeWebSocket:
            async def send(self, payload: str) -> None:
                sent.append(payload)

        asyncio.run(
            stream._subscribe_option(
                _FakeWebSocket(), "SPY", date(2026, 9, 18), ContractType.CALL, Decimal(770), "TRADE"
            )
        )

        payload = json.loads(sent[0])
        assert payload == {
            "msg_type": "STREAM",
            "sec_type": "OPTION",
            "req_type": "TRADE",
            "add": True,
            "id": 1,
            "contract": {
                "root": "SPY",
                "expiration": 20260918,
                "strike": 770000,
                "right": "C",
            },
        }

    def test_reconcile_logs_warning_on_large_discrepancy(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "response": [
                        {"data": [{"volume": 1000}, {"volume": 1000}]}
                    ]
                },
            )

        client = httpx.Client(base_url=REST_URL, transport=httpx.MockTransport(handler))
        stream = ThetaStreamHub(WS_URL, client)
        stream.register_contract(
            "SPY260918C00770000", "SPY", date(2026, 9, 18), ContractType.CALL, Decimal(770)
        )
        stream._cumulative_volume["SPY260918C00770000"] = 500  # way below REST's 2000

        with caplog.at_level(logging.WARNING):
            stream._reconcile()

        assert any("mismatch" in record.message for record in caplog.records)

    def test_reconcile_logs_info_when_volumes_are_close(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"response": [{"data": [{"volume": 1000}]}]})

        client = httpx.Client(base_url=REST_URL, transport=httpx.MockTransport(handler))
        stream = ThetaStreamHub(WS_URL, client)
        stream.register_contract(
            "SPY260918C00770000", "SPY", date(2026, 9, 18), ContractType.CALL, Decimal(770)
        )
        stream._cumulative_volume["SPY260918C00770000"] = 995

        with caplog.at_level(logging.INFO):
            stream._reconcile()

        assert any("reconciled" in record.message for record in caplog.records)
        assert not any(record.levelno == logging.WARNING for record in caplog.records)


class TestReconcileScheduling:
    """Fix (2026-09-10): reconcile() used to run inline in _consume()'s
    own read loop, confirmed live (2026-09-09 into 2026-09-10, ~48
    cycles overnight) to block websocket.recv() for 44-55s every single
    cycle -- 300%+ over STATUS_STALE_AFTER_SECONDS, stalling all 3
    logical streams at once. It now runs on its own independent task
    (_run_reconcile_loop), started by start() alongside the watchdog,
    never inside _consume()."""

    def test_consume_no_longer_calls_reconcile_inline(self) -> None:
        """Regression guard for the actual fix -- reads the compiled
        source directly, the same convention already used for
        confirming the REQ_RESPONSE routing exists in _consume()."""
        import inspect

        source = inspect.getsource(ThetaStreamHub._consume)
        assert "_reconcile" not in source
        assert "asyncio.to_thread" not in source

    @pytest.mark.asyncio
    async def test_reconcile_loop_sleeps_the_configured_interval_before_each_cycle(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stream = ThetaStreamHub(WS_URL, httpx.Client(base_url=REST_URL))
        sleep_calls: list[float] = []
        reconcile_calls = {"n": 0}

        async def fake_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)
            if len(sleep_calls) >= 2:
                raise asyncio.CancelledError

        def fake_reconcile() -> None:
            reconcile_calls["n"] += 1

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)
        monkeypatch.setattr(stream, "_reconcile", fake_reconcile)

        with pytest.raises(asyncio.CancelledError):
            await stream._run_reconcile_loop()

        assert sleep_calls == [
            provider_module.RECONCILE_INTERVAL_SECONDS,
            provider_module.RECONCILE_INTERVAL_SECONDS,
        ]
        assert reconcile_calls["n"] == 1

    @pytest.mark.asyncio
    async def test_reconcile_loop_survives_an_unexpected_exception(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The same class of bug already fixed once in this incident
        (WhaleAlertsStreamManager/UnderlyingPriceStreamManager's own
        per-symbol tasks) must not be reintroduced here: one bad cycle
        must not silently end this periodic job forever."""
        stream = ThetaStreamHub(WS_URL, httpx.Client(base_url=REST_URL))
        sleep_calls: list[float] = []
        reconcile_calls = {"n": 0}

        async def fake_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)
            # 3 sleeps, not 2: the 1st reconcile (after sleep #1) fails,
            # so a 2nd reconcile (after sleep #2) is what actually proves
            # the loop kept going -- cancelling on sleep #2 instead would
            # only prove the loop reached the top again, not that it
            # tried reconcile() a second time.
            if len(sleep_calls) >= 3:
                raise asyncio.CancelledError

        def flaky_reconcile() -> None:
            reconcile_calls["n"] += 1
            if reconcile_calls["n"] == 1:
                raise RuntimeError("simulated reconcile failure")

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)
        monkeypatch.setattr(stream, "_reconcile", flaky_reconcile)

        with caplog.at_level(logging.ERROR), pytest.raises(asyncio.CancelledError):
            await stream._run_reconcile_loop()

        assert reconcile_calls["n"] == 2, "the loop must keep running after the first failure"
        assert any("failed unexpectedly" in record.message for record in caplog.records)

    @pytest.mark.asyncio
    async def test_start_and_stop_manage_the_reconcile_task(self) -> None:
        stream = ThetaStreamHub(WS_URL, httpx.Client(base_url=REST_URL))
        stream.start()
        reconcile_task = stream._reconcile_task
        assert reconcile_task is not None
        assert not reconcile_task.done()

        await stream.stop()

        assert stream._reconcile_task is None
        assert reconcile_task.cancelled()


class TestQueueBackpressure:
    """Fix (2026-09-10): every subscriber queue used to be unbounded.
    Fine while reconcile() was the only thing that could ever make a
    consumer fall behind (see TestReconcileScheduling) -- once that's
    fixed, an unbounded queue stops being a safety net and starts being
    a silent, unlimited memory leak if a consumer genuinely gets stuck
    for an unrelated reason. TRADE/QUOTE (critical -- must not lose real
    messages) alert loudly and drop the one message when truly full;
    the underlying-price queue (only the latest price has any value for
    a 0DTE chart) drops the oldest, silently, by design."""

    def test_subscribed_queues_are_bounded_to_the_documented_sizes(self) -> None:
        stream = ThetaStreamHub(WS_URL, httpx.Client(base_url=REST_URL))

        assert stream.subscribe_trade_queue("SPY").maxsize == provider_module.TRADE_QUEUE_MAXSIZE
        assert stream.subscribe_quote_queue("SPY").maxsize == provider_module.QUOTE_QUEUE_MAXSIZE
        assert (
            stream.subscribe_underlying_queue("SPY").maxsize
            == provider_module.UNDERLYING_QUEUE_MAXSIZE
        )

    def test_dispatch_critical_drops_and_logs_critical_when_the_queue_is_full(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=1)
        queue.put_nowait("already queued")
        stream = ThetaStreamHub(WS_URL, httpx.Client(base_url=REST_URL))

        with caplog.at_level(logging.CRITICAL):
            stream._dispatch_critical([queue], "dropped", "QUOTE", "SPY")

        assert queue.qsize() == 1
        assert queue.get_nowait() == "already queued", "the queued item must not be disturbed"
        critical_records = [r for r in caplog.records if r.levelno == logging.CRITICAL]
        assert critical_records
        # The symbol responsible must be identifiable from the log alone --
        # confirmed live, 2026-09-10, that an aggregate-only message
        # couldn't say which symbol's queue was actually overflowing.
        assert "SPY" in critical_records[0].getMessage()

    def test_dispatch_critical_does_not_raise_when_the_queue_is_full(self) -> None:
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=1)
        queue.put_nowait("already queued")
        stream = ThetaStreamHub(WS_URL, httpx.Client(base_url=REST_URL))

        stream._dispatch_critical([queue], "dropped", "QUOTE", "SPY")  # must not raise

    def test_dispatch_dropping_keeps_only_the_newest_items_in_fifo_order(self) -> None:
        queue: asyncio.Queue[int] = asyncio.Queue(maxsize=3)
        stream = ThetaStreamHub(WS_URL, httpx.Client(base_url=REST_URL))

        for value in (1, 2, 3, 4, 5):
            stream._dispatch_dropping([queue], value, "underlying TRADE")

        # Oldest two (1, 2) were dropped as the queue filled; the
        # remaining 3 keep their original relative (FIFO) order.
        assert queue.qsize() == 3
        assert [queue.get_nowait() for _ in range(3)] == [3, 4, 5]

    def test_dispatch_dropping_does_not_log_critical(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Overflow here is the designed-for behavior, not a symptom --
        must never alarm the way _dispatch_critical does."""
        queue: asyncio.Queue[int] = asyncio.Queue(maxsize=1)
        stream = ThetaStreamHub(WS_URL, httpx.Client(base_url=REST_URL))

        with caplog.at_level(logging.CRITICAL):
            stream._dispatch_dropping([queue], 1, "underlying TRADE")
            stream._dispatch_dropping([queue], 2, "underlying TRADE")

        assert not any(record.levelno == logging.CRITICAL for record in caplog.records)

    def test_log_queue_depths_names_the_symbol_with_the_fullest_queue(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Fix (2026-09-10): the periodic queue-depth log used to report
        only aggregate stats (max/total/fullest_pct) per queue TYPE, with
        no way to tell which symbol's queue was actually the fullest one
        -- confirmed live this couldn't answer "which symbol is
        responsible," which selective (per-symbol) batching needs to
        know rather than assuming it's SPX from this project's history."""
        stream = ThetaStreamHub(WS_URL, httpx.Client(base_url=REST_URL))
        quiet_queue = stream.subscribe_trade_queue("AAPL")
        quiet_queue.put_nowait("one")
        busy_queue = stream.subscribe_trade_queue("SPX")
        busy_queue.put_nowait("one")
        busy_queue.put_nowait("two")
        busy_queue.put_nowait("three")

        with caplog.at_level(logging.INFO):
            stream._log_queue_depths()

        info_records = [r for r in caplog.records if r.levelno == logging.INFO]
        assert any("SPX" in r.getMessage() for r in info_records)


class TestStreamHubReconnection:
    """Backoff/reset now lives in exactly one _run() loop, shared by all
    3 logical streams (see ThetaStreamHub's own docstring for why there's
    only one connection) -- one test pair covers it, where before there
    were 3 near-identical copies, one per now-deleted class."""

    @pytest.mark.asyncio
    async def test_run_backs_off_exponentially_between_reconnect_attempts(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stream = ThetaStreamHub(WS_URL, httpx.Client(base_url=REST_URL))
        sleep_calls: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)
            if len(sleep_calls) >= 3:
                raise asyncio.CancelledError

        async def failing_connect() -> None:
            raise ConnectionError("simulated disconnect")

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)
        monkeypatch.setattr(stream, "_connect_and_consume", failing_connect)

        with pytest.raises(asyncio.CancelledError):
            await stream._run()

        assert sleep_calls[0] == 2
        assert sleep_calls[1] == 4
        assert sleep_calls[2] == 8

    @pytest.mark.asyncio
    async def test_run_keeps_escalating_on_rapid_failures_but_resets_after_a_stable_connection(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The actual fix, confirmed live 2026-09 from the real Worker's
        own log: _run()'s `delay = RECONNECT_BASE_DELAY_SECONDS` right
        after `await self._connect_and_consume()` was unreachable dead
        code (_connect_and_consume() never returns normally, only ever
        raises) -- once anything pushed delay up, it climbed forever,
        stuck at RECONNECT_MAX_DELAY_SECONDS for the rest of the
        process's life regardless of how a later connection actually
        behaved. Two connections that fail almost immediately (real
        problem, keep backing off) followed by one that stays up well
        past STABLE_CONNECTION_RESET_SECONDS before failing (a fresh,
        unrelated hiccup) must reset to the base delay for what comes
        after it, not continue escalating from 8s."""
        stream = ThetaStreamHub(WS_URL, httpx.Client(base_url=REST_URL))
        clock = _FakeMonotonicClock()
        monkeypatch.setattr(provider_module.time, "monotonic", clock)

        sleep_calls: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)

        attempt = {"n": 0}

        async def fake_connect_and_consume() -> None:
            attempt["n"] += 1
            if attempt["n"] in (1, 2):
                clock.advance(1)  # fails almost immediately both times
                raise ConnectionError(f"quick failure {attempt['n']}")
            if attempt["n"] == 3:
                # Stays up well past the reset threshold this time.
                clock.advance(provider_module.STABLE_CONNECTION_RESET_SECONDS + 5)
                raise ConnectionError("failure after a stable stretch")
            raise asyncio.CancelledError

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)
        monkeypatch.setattr(stream, "_connect_and_consume", fake_connect_and_consume)

        with pytest.raises(asyncio.CancelledError):
            await stream._run()

        # 2 -> 4 (still escalating, both quick failures) -> 2 (reset,
        # the third connection was stable for a while before it failed).
        assert sleep_calls == [2, 4, 2]

    @pytest.mark.asyncio
    async def test_start_and_stop_manage_a_single_background_task(self) -> None:
        stream = ThetaStreamHub(WS_URL, httpx.Client(base_url=REST_URL))
        stream.start()
        task = stream._task
        assert task is not None

        # A second start() while already running is a no-op.
        stream.start()
        assert stream._task is task

        await stream.stop()
        assert stream._task is None

    @pytest.mark.asyncio
    async def test_start_and_stop_also_manage_the_watchdog_task(self) -> None:
        stream = ThetaStreamHub(WS_URL, httpx.Client(base_url=REST_URL))
        stream.start()
        watchdog_task = stream._watchdog_task
        assert watchdog_task is not None
        assert not watchdog_task.done()

        await stream.stop()

        assert stream._watchdog_task is None
        assert watchdog_task.cancelled()

    def test_request_reconnect_is_a_no_op_before_start(self) -> None:
        # Near-the-money re-subscription (get_option_chain) and the
        # data-silence watchdog both call this unconditionally -- must
        # never raise for the API process's own dormant ThetaDataProvider
        # instance (never started, per backend/main.py's lifespan) or
        # before the Worker's own connection has been made for the first
        # time.
        stream = ThetaStreamHub(WS_URL, httpx.Client(base_url=REST_URL))
        stream.request_reconnect()  # must not raise

    @pytest.mark.asyncio
    async def test_request_reconnect_closes_the_active_connection(self) -> None:
        class _FakeWebsocket:
            def __init__(self) -> None:
                self.closed = False

            async def close(self) -> None:
                self.closed = True

        stream = ThetaStreamHub(WS_URL, httpx.Client(base_url=REST_URL))
        fake_websocket = _FakeWebsocket()
        stream._loop = asyncio.get_running_loop()
        stream._active_websocket = fake_websocket  # type: ignore[assignment]

        stream.request_reconnect()
        # request_reconnect schedules the close via run_coroutine_threadsafe
        # (safe to call from a different thread than the event loop's own,
        # which get_option_chain's caller -- a scheduler worker thread --
        # actually is) rather than awaiting it directly -- give the loop a
        # moment to actually run the scheduled coroutine.
        await asyncio.sleep(0.05)

        assert fake_websocket.closed is True


class TestStreamHubDataSilenceWatchdog:
    """One watchdog task now tracks 3 independent timestamps (QUOTE,
    option TRADE, underlying TRADE) instead of 3 separate classes each
    tracking their own -- see ThetaStreamHub._watch_for_data_silence's
    own docstring. Confirmed live, 2026-09-09, real market open: this
    mechanism (previously present on 2 of the 3 logical streams) was
    missing entirely for option TRADE, and that was the direct cause of
    a full session with zero Whale Alerts -- ThetaStreamHub now has it
    for all 3."""

    @pytest.mark.asyncio
    async def test_watchdog_forces_reconnect_after_quote_silence_during_market_hours(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stream = ThetaStreamHub(WS_URL, httpx.Client(base_url=REST_URL))
        reconnects: list[int] = []
        monkeypatch.setattr(stream, "request_reconnect", lambda: reconnects.append(1))
        monkeypatch.setattr(provider_module, "is_market_open", lambda now: True)
        stream._last_quote_at = (
            time.monotonic() - provider_module.DATA_SILENCE_THRESHOLD_SECONDS - 1
        )

        monkeypatch.setattr(asyncio, "sleep", _fake_sleep_letting_n_iterations_run(1))

        with pytest.raises(asyncio.CancelledError):
            await stream._watch_for_data_silence()

        assert reconnects == [1]

    @pytest.mark.asyncio
    async def test_watchdog_forces_reconnect_after_option_trade_silence_during_market_hours(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stream = ThetaStreamHub(WS_URL, httpx.Client(base_url=REST_URL))
        reconnects: list[int] = []
        monkeypatch.setattr(stream, "request_reconnect", lambda: reconnects.append(1))
        monkeypatch.setattr(provider_module, "is_market_open", lambda now: True)
        stream._last_option_trade_at = (
            time.monotonic() - provider_module.DATA_SILENCE_THRESHOLD_SECONDS - 1
        )

        monkeypatch.setattr(asyncio, "sleep", _fake_sleep_letting_n_iterations_run(1))

        with pytest.raises(asyncio.CancelledError):
            await stream._watch_for_data_silence()

        assert reconnects == [1]

    @pytest.mark.asyncio
    async def test_watchdog_forces_reconnect_after_underlying_trade_silence_during_market_hours(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The actual fix, confirmed live 2026-09 with an instrumented
        reproduction: Theta Terminal can stop delivering TRADE messages
        to this connection (while its STATUS heartbeat stays perfectly
        healthy) after a *different* logical stream's own subscribe
        burst. Manually forcing a reconnect restored delivery every time
        it was tried live -- this test confirms the watchdog automates
        exactly that action once silence crosses the threshold."""
        stream = ThetaStreamHub(WS_URL, httpx.Client(base_url=REST_URL))
        reconnects: list[int] = []
        monkeypatch.setattr(stream, "request_reconnect", lambda: reconnects.append(1))
        monkeypatch.setattr(provider_module, "is_market_open", lambda now: True)
        stream._last_underlying_trade_at = (
            time.monotonic() - provider_module.DATA_SILENCE_THRESHOLD_SECONDS - 1
        )

        monkeypatch.setattr(asyncio, "sleep", _fake_sleep_letting_n_iterations_run(1))

        with pytest.raises(asyncio.CancelledError):
            await stream._watch_for_data_silence()

        assert reconnects == [1]

    @pytest.mark.asyncio
    async def test_watchdog_does_not_reconnect_while_the_market_is_closed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A genuinely quiet closed market must never trip this, on any of
        # the 3 tracked streams -- the explicit "don't fire unnecessary
        # reconnects" requirement.
        stream = ThetaStreamHub(WS_URL, httpx.Client(base_url=REST_URL))
        reconnects: list[int] = []
        monkeypatch.setattr(stream, "request_reconnect", lambda: reconnects.append(1))
        monkeypatch.setattr(provider_module, "is_market_open", lambda now: False)
        stale = time.monotonic() - provider_module.DATA_SILENCE_THRESHOLD_SECONDS - 1
        stream._last_quote_at = stale
        stream._last_option_trade_at = stale
        stream._last_underlying_trade_at = stale

        monkeypatch.setattr(asyncio, "sleep", _fake_sleep_letting_n_iterations_run(1))

        with pytest.raises(asyncio.CancelledError):
            await stream._watch_for_data_silence()

        assert reconnects == []

    @pytest.mark.asyncio
    async def test_watchdog_does_not_reconnect_before_the_first_message_of_any_kind_arrives(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # All 3 timestamps are None until their respective first message
        # -- a fresh connection that hasn't had a chance to receive
        # anything yet must not be judged as "silent".
        stream = ThetaStreamHub(WS_URL, httpx.Client(base_url=REST_URL))
        reconnects: list[int] = []
        monkeypatch.setattr(stream, "request_reconnect", lambda: reconnects.append(1))
        monkeypatch.setattr(provider_module, "is_market_open", lambda now: True)
        assert stream._last_quote_at is None
        assert stream._last_option_trade_at is None
        assert stream._last_underlying_trade_at is None

        monkeypatch.setattr(asyncio, "sleep", _fake_sleep_letting_n_iterations_run(1))

        with pytest.raises(asyncio.CancelledError):
            await stream._watch_for_data_silence()

        assert reconnects == []

    @pytest.mark.asyncio
    async def test_watchdog_does_not_reconnect_while_messages_are_still_arriving(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stream = ThetaStreamHub(WS_URL, httpx.Client(base_url=REST_URL))
        reconnects: list[int] = []
        monkeypatch.setattr(stream, "request_reconnect", lambda: reconnects.append(1))
        monkeypatch.setattr(provider_module, "is_market_open", lambda now: True)
        fresh = time.monotonic()  # well under the threshold
        stream._last_quote_at = fresh
        stream._last_option_trade_at = fresh
        stream._last_underlying_trade_at = fresh

        monkeypatch.setattr(asyncio, "sleep", _fake_sleep_letting_n_iterations_run(1))

        with pytest.raises(asyncio.CancelledError):
            await stream._watch_for_data_silence()

        assert reconnects == []


class TestQuoteHandling:
    def test_handle_quote_publishes_a_quote_event_to_subscribers(self) -> None:
        stream = ThetaStreamHub(WS_URL, httpx.Client(base_url=REST_URL))
        queue = stream.subscribe_quote_queue("SPY")
        message = {
            "header": {"type": "QUOTE", "status": "CONNECTED"},
            "contract": {
                "security_type": "OPTION",
                "root": "SPY",
                "expiration": 20260918,
                "strike": 770000,
                "right": "C",
            },
            "quote": {
                "ms_of_day": 26622025,
                "bid_size": 7,
                "bid": 1.08,
                "ask_size": 7,
                "ask": 1.09,
                "date": 20261219,
            },
        }

        stream._handle_quote(message)
        event = queue.get_nowait()

        occ = _build_occ_symbol("SPY", date(2026, 9, 18), ContractType.CALL, Decimal(770))
        assert event.symbol == "SPY"
        assert event.occ_symbol == occ
        assert event.bid == Decimal("1.08")
        assert event.ask == Decimal("1.09")

    def test_handle_quote_ignores_incomplete_messages(self) -> None:
        stream = ThetaStreamHub(WS_URL, httpx.Client(base_url=REST_URL))
        queue = stream.subscribe_quote_queue("SPY")

        stream._handle_quote({"contract": {"root": "SPY"}, "quote": {"bid": 1.08}})

        assert queue.empty()

    def test_subscribe_option_quote_sends_the_documented_quote_payload(self) -> None:
        stream = ThetaStreamHub(WS_URL, httpx.Client(base_url=REST_URL))
        sent: list[str] = []

        class _FakeWebSocket:
            async def send(self, payload: str) -> None:
                sent.append(payload)

        asyncio.run(
            stream._subscribe_option(
                _FakeWebSocket(), "SPY", date(2026, 9, 18), ContractType.CALL, Decimal(770), "QUOTE"
            )
        )

        payload = json.loads(sent[0])
        assert payload["req_type"] == "QUOTE"
        assert payload["sec_type"] == "OPTION"


class _FakeMonotonicClock:
    """A controllable stand-in for time.monotonic() -- lets a test decide
    exactly how much (simulated) time a connection stayed alive before
    failing, to test STABLE_CONNECTION_RESET_SECONDS's reset logic
    deterministically instead of waiting on a real wall-clock."""

    def __init__(self, start: float = 0.0) -> None:
        self._value = start

    def __call__(self) -> float:
        return self._value

    def advance(self, seconds: float) -> None:
        self._value += seconds


def _fake_sleep_letting_n_iterations_run(iterations: int = 1):
    """An asyncio.sleep fake for testing a `while True: await
    asyncio.sleep(...); <body>` loop (the watchdogs below): returns
    normally for the first `iterations` calls, so the loop's own body
    actually executes that many times for real, then raises
    CancelledError to break out of the otherwise-infinite loop --
    without this, a fake that raises on the very first call never lets
    the body run at all, and a test asserting "didn't reconnect" would
    pass even if the watchdog's check logic were completely broken."""
    calls = {"n": 0}

    async def fake_sleep(seconds: float) -> None:
        calls["n"] += 1
        if calls["n"] > iterations:
            raise asyncio.CancelledError

    return fake_sleep


class TestUnderlyingTradeHandling:
    """Fixtures below were built from ThetaData's public v3 docs
    (https://docs.thetadata.us/Streaming/US-Stocks/Trade-Stream.html)
    and confirmed live against a real Theta Terminal, market open
    (2026-09-03, Stocks+Index plans active): SPY/TSLA (STOCK) and
    SPX/VIX/NDX (INDEX) all delivered genuine TRADE messages in exactly
    this shape, size=0 confirmed for every INDEX message.

    A real production bug was found and fixed here (2026-09-03): the
    local Theta Terminal broadcasts every symbol/contract with an active
    subscription ANYWHERE on that Terminal to EVERY connected WebSocket
    client, not just what a given connection itself subscribed to (now,
    2026-09-09, the single ThetaStreamHub connection carries all of it,
    so this is by design, not a leak). A STOCK/INDEX-shaped trade for a
    root doesn't guarantee it matches THIS symbol's own registered kind
    -- `_handle_underlying_trade` checks `contract.security_type`
    against the registered `UnderlyingKind` for exactly that reason; see
    test_option_trades_sharing_the_same_root_are_filtered_out and its
    neighbors below, which also register a symbol/kind before calling
    `_handle_underlying_trade` (an unregistered symbol has no expected
    `security_type` to validate against, so it's dropped -- see
    test_trade_for_an_unregistered_symbol_is_dropped_not_guessed)."""

    def test_handle_underlying_trade_publishes_an_event_to_subscribers(self) -> None:
        stream = ThetaStreamHub(WS_URL, httpx.Client(base_url=REST_URL))
        stream.register_symbol("AAPL", UnderlyingKind.EQUITY)
        queue = stream.subscribe_underlying_queue("AAPL")
        # Exact shape from ThetaData's docs' own example message.
        message = {
            "header": {"type": "TRADE", "status": "CONNECTED"},
            "contract": {"security_type": "STOCK", "root": "AAPL"},
            "trade": {
                "ms_of_day": 38437607,
                "sequence": 12150295,
                "size": 500,
                "condition": 0,
                "price": 184.5099,
                "exchange": 57,
                "date": 20240503,
            },
        }

        stream._handle_underlying_trade(message)
        event = queue.get_nowait()

        assert event.symbol == "AAPL"
        assert event.price == Decimal("184.5099")
        assert event.size == 500

    def test_handle_underlying_trade_only_publishes_to_the_matching_symbols_subscribers(
        self,
    ) -> None:
        stream = ThetaStreamHub(WS_URL, httpx.Client(base_url=REST_URL))
        stream.register_symbol("AAPL", UnderlyingKind.EQUITY)
        aapl_queue = stream.subscribe_underlying_queue("AAPL")
        spy_queue = stream.subscribe_underlying_queue("SPY")
        message = {
            "header": {"type": "TRADE", "status": "CONNECTED"},
            "contract": {"security_type": "STOCK", "root": "AAPL"},
            "trade": {"size": 1, "price": 100.0},
        }

        stream._handle_underlying_trade(message)

        assert not aapl_queue.empty()
        assert spy_queue.empty()

    def test_option_trades_sharing_the_same_root_are_filtered_out(self) -> None:
        """Regression test for a real production bug (confirmed live,
        2026-09-03 market open, see this class's own docstring): before
        the fix, this handler only checked contract.root, so this exact
        VIX call option trade (captured live) was accepted and published
        as if it were VIX's own price ($1.57 published as "VIX price",
        real VIX index level ~14.87-14.89 at the same moment). Now it
        also checks contract.security_type against the registered
        UnderlyingKind, so an OPTION trade sharing the root must be
        dropped instead. _consume() itself already keeps OPTION-typed
        TRADE messages from ever reaching this handler in the running
        system -- this drives the handler directly to also cover a
        STOCK/INDEX-shaped message for a root that simply doesn't match
        the registered kind."""
        stream = ThetaStreamHub(WS_URL, httpx.Client(base_url=REST_URL))
        stream.register_symbol("VIX", UnderlyingKind.INDEX)
        queue = stream.subscribe_underlying_queue("VIX")
        # Exact shape captured live -- a VIX call option trade, not the
        # VIX index itself, sharing contract.root == "VIX".
        message = {
            "header": {"type": "TRADE", "status": "CONNECTED"},
            "contract": {
                "security_type": "OPTION",
                "root": "VIX",
                "expiration": 20260916,
                "strike": 15000,
                "right": "C",
            },
            "trade": {
                "ms_of_day": 42308474,
                "sequence": 697107163,
                "size": 10,
                "condition": 18,
                "price": 1.57,
                "exchange": 5,
                "date": 20260903,
            },
        }

        stream._handle_underlying_trade(message)

        assert queue.empty()

    def test_genuine_index_trade_for_a_registered_symbol_still_publishes(self) -> None:
        """The fix must not be overly broad -- a genuine INDEX trade for
        VIX (security_type matching the registered UnderlyingKind.INDEX)
        must still publish normally. Exact shape captured live alongside
        the option-contamination messages above."""
        stream = ThetaStreamHub(WS_URL, httpx.Client(base_url=REST_URL))
        stream.register_symbol("VIX", UnderlyingKind.INDEX)
        queue = stream.subscribe_underlying_queue("VIX")
        message = {
            "header": {"type": "TRADE", "status": "CONNECTED"},
            "contract": {"security_type": "INDEX", "root": "VIX"},
            "trade": {
                "ms_of_day": 42331000,
                "sequence": 0,
                "size": 0,
                "condition": 0,
                "price": 14.88,
                "exchange": 5,
                "date": 20260903,
            },
        }

        stream._handle_underlying_trade(message)
        event = queue.get_nowait()

        assert event.symbol == "VIX"
        assert event.price == Decimal("14.88")

    def test_option_trade_for_an_equity_root_is_also_filtered_out(self) -> None:
        """Same fix, STOCK side -- confirmed live that root="SPX" (an
        INDEX) also saw OPTION contamination (13 of 139 messages over
        60s), and the same leak mechanism applies to any registered
        EQUITY symbol whose options are subscribed elsewhere on the
        same Terminal."""
        stream = ThetaStreamHub(WS_URL, httpx.Client(base_url=REST_URL))
        stream.register_symbol("SPY", UnderlyingKind.EQUITY)
        queue = stream.subscribe_underlying_queue("SPY")
        message = {
            "header": {"type": "TRADE", "status": "CONNECTED"},
            "contract": {
                "security_type": "OPTION",
                "root": "SPY",
                "expiration": 20260918,
                "strike": 770000,
                "right": "C",
            },
            "trade": {"size": 10, "price": 1.09},
        }

        stream._handle_underlying_trade(message)

        assert queue.empty()

    def test_trade_for_an_unregistered_symbol_is_dropped_not_guessed(self) -> None:
        """No registered UnderlyingKind means no expected security_type
        to validate against -- dropping is the safe default, not a
        guess. Harmless either way (no subscriber would exist for an
        unregistered symbol), but explicit is better than relying on
        that coincidence."""
        stream = ThetaStreamHub(WS_URL, httpx.Client(base_url=REST_URL))
        queue = stream.subscribe_underlying_queue("QQQ")
        message = {
            "header": {"type": "TRADE", "status": "CONNECTED"},
            "contract": {"security_type": "STOCK", "root": "QQQ"},
            "trade": {"size": 10, "price": 500.0},
        }

        stream._handle_underlying_trade(message)

        assert queue.empty()

    def test_handle_underlying_trade_ignores_incomplete_messages(self) -> None:
        stream = ThetaStreamHub(WS_URL, httpx.Client(base_url=REST_URL))
        queue = stream.subscribe_underlying_queue("AAPL")

        stream._handle_underlying_trade({"contract": {"root": "AAPL"}, "trade": {"size": 500}})

        assert queue.empty()

    def test_subscribe_underlying_sends_the_documented_stock_trade_stream_payload(self) -> None:
        stream = ThetaStreamHub(WS_URL, httpx.Client(base_url=REST_URL))
        sent: list[str] = []

        class _FakeWebSocket:
            async def send(self, payload: str) -> None:
                sent.append(payload)

        asyncio.run(stream._subscribe_underlying(_FakeWebSocket(), "AAPL", UnderlyingKind.EQUITY))

        payload = json.loads(sent[0])
        # Exact shape from ThetaData's docs — a stock's "contract" is
        # just its root symbol, no expiration/strike/right.
        assert payload == {
            "msg_type": "STREAM",
            "sec_type": "STOCK",
            "req_type": "TRADE",
            "add": True,
            "id": 1,
            "contract": {"root": "AAPL"},
        }

    def test_subscribe_underlying_uses_sec_type_index_for_index_underlyings(self) -> None:
        # Confirmed from ThetaData's docs: indices use a genuinely
        # separate stream (US-Indices Price Stream, its own "Index
        # Standard" subscription) — sec_type is the only field that
        # differs from the stock variant, the trade message shape itself
        # is identical.
        stream = ThetaStreamHub(WS_URL, httpx.Client(base_url=REST_URL))
        sent: list[str] = []

        class _FakeWebSocket:
            async def send(self, payload: str) -> None:
                sent.append(payload)

        asyncio.run(stream._subscribe_underlying(_FakeWebSocket(), "SPX", UnderlyingKind.INDEX))

        payload = json.loads(sent[0])
        assert payload["sec_type"] == "INDEX"
        assert payload["contract"] == {"root": "SPX"}


class TestProviderLifecycle:
    @pytest.mark.asyncio
    async def test_stop_closes_the_rest_client_and_hub(self) -> None:
        provider = ThetaDataProvider(REST_URL, WS_URL)
        await provider.stop()
        assert provider._client.is_closed
        assert provider._hub._task is None

    @pytest.mark.asyncio
    async def test_stream_underlying_trades_yields_events_from_the_queue(self) -> None:
        provider = ThetaDataProvider(REST_URL, WS_URL)
        provider._hub.register_symbol("SPY", UnderlyingKind.EQUITY)
        events = provider.stream_underlying_trades("SPY")
        # Advance the async generator to its subscribe_underlying_queue()
        # + first `await queue.get()` before publishing — otherwise the
        # event below would be put_nowait'd to a queue nothing has
        # subscribed to yet and silently dropped.
        pending = asyncio.ensure_future(events.__anext__())
        await asyncio.sleep(0)

        message = {
            "header": {"type": "TRADE", "status": "CONNECTED"},
            "contract": {"security_type": "STOCK", "root": "SPY"},
            "trade": {"size": 100, "price": 552.25},
        }
        provider._hub._handle_underlying_trade(message)
        event = await asyncio.wait_for(pending, timeout=1)

        assert event.symbol == "SPY"
        assert event.price == Decimal("552.25")
        assert event.size == 100

    @pytest.mark.asyncio
    async def test_start_registers_every_symbol_except_futures_with_the_right_kind(self) -> None:
        # A generic success response for every symbol's discovery calls
        # — the registration loop doesn't validate the response's own
        # "symbol"/"root" fields match the requested one, only the outer
        # ACTIVE_UNDERLYINGS_BY_SYMBOL loop variable is used to build
        # each occ_symbol.
        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if "greeks/first_order" in url:
                return httpx.Response(200, json={"response": [_first_order_entry("100.00", "CALL")]})
            if "open_interest" in url:
                return httpx.Response(200, json={"response": []})
            if "interest_rate/history/eod" in url:
                return httpx.Response(
                    200, json={"response": [{"rate": 3.64, "created": "2026-08-31"}]}
                )
            if "history/eod" in url:
                return httpx.Response(200, json=_daily_bars_response())
            raise AssertionError(f"unexpected request: {request.url}")

        provider = _provider_with_transport(handler)
        try:
            await provider.start()

            registered = provider._hub._symbols
            assert "ES" not in registered  # FUTURE — no confirmed stream type
            assert registered["SPY"] == UnderlyingKind.EQUITY
            assert registered["SPX"] == UnderlyingKind.INDEX
            assert registered["VIX"] == UnderlyingKind.INDEX
        finally:
            await provider.stop()


class TestNearTheMoneyWidthFiltering:
    def test_overfetches_with_the_generous_strike_range(self) -> None:
        seen_strike_range = None

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal seen_strike_range
            if "greeks/first_order" in str(request.url):
                seen_strike_range = request.url.params.get("strike_range")
                return httpx.Response(
                    200, json={"response": [_first_order_entry("769.00", "CALL")]}
                )
            if "open_interest" in str(request.url):
                return httpx.Response(200, json={"response": []})
            if "interest_rate/history/eod" in str(request.url):
                return httpx.Response(
                    200, json={"response": [{"rate": 3.64, "created": "2026-08-31"}]}
                )
            if "stock/history/eod" in str(request.url):
                return httpx.Response(200, json=_daily_bars_response())
            raise AssertionError(f"unexpected request: {request.url}")

        provider = _provider_with_transport(handler)
        provider.get_option_chain("SPY")

        assert seen_strike_range == "100"

    def test_filters_out_strikes_beyond_the_atr_derived_width(self) -> None:
        # $2 daily range -> ATR=2 -> width=3 (ATR_WIDTH_MULTIPLIER=1.5).
        # Spot is 769.36 (the fixture default) -- 769.00 is within width
        # (diff 0.36), 900.00 is nowhere close.
        def handler(request: httpx.Request) -> httpx.Response:
            if "greeks/first_order" in str(request.url):
                return httpx.Response(
                    200,
                    json={
                        "response": [
                            _first_order_entry("769.00", "CALL"),
                            _first_order_entry("900.00", "CALL"),
                        ]
                    },
                )
            if "open_interest" in str(request.url):
                return httpx.Response(200, json={"response": []})
            if "interest_rate/history/eod" in str(request.url):
                return httpx.Response(
                    200, json={"response": [{"rate": 3.64, "created": "2026-08-31"}]}
                )
            if "stock/history/eod" in str(request.url):
                return httpx.Response(200, json=_daily_bars_response())
            raise AssertionError(f"unexpected request: {request.url}")

        provider = _provider_with_transport(handler)
        chain = provider.get_option_chain("SPY")

        assert len(chain.contracts) == 1
        assert chain.contracts[0].strike == Decimal("769.00")

    def test_caches_the_width_per_day_without_a_second_daily_bars_request(self) -> None:
        history_request_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal history_request_count
            if "greeks/first_order" in str(request.url):
                return httpx.Response(
                    200, json={"response": [_first_order_entry("769.00", "CALL")]}
                )
            if "open_interest" in str(request.url):
                return httpx.Response(200, json={"response": []})
            if "interest_rate/history/eod" in str(request.url):
                return httpx.Response(
                    200, json={"response": [{"rate": 3.64, "created": "2026-08-31"}]}
                )
            if "stock/history/eod" in str(request.url):
                history_request_count += 1
                return httpx.Response(200, json=_daily_bars_response())
            raise AssertionError(f"unexpected request: {request.url}")

        provider = _provider_with_transport(handler)
        provider.get_option_chain("SPY")
        provider.get_option_chain("SPY")

        assert history_request_count == 1

    def test_logs_the_computed_width_on_first_calculation(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if "greeks/first_order" in str(request.url):
                return httpx.Response(
                    200, json={"response": [_first_order_entry("769.00", "CALL")]}
                )
            if "open_interest" in str(request.url):
                return httpx.Response(200, json={"response": []})
            if "interest_rate/history/eod" in str(request.url):
                return httpx.Response(
                    200, json={"response": [{"rate": 3.64, "created": "2026-08-31"}]}
                )
            if "stock/history/eod" in str(request.url):
                return httpx.Response(200, json=_daily_bars_response())
            raise AssertionError(f"unexpected request: {request.url}")

        provider = _provider_with_transport(handler)
        with caplog.at_level(logging.INFO):
            provider.get_option_chain("SPY")

        assert any("Near-the-money width for SPY" in record.message for record in caplog.records)

    def test_guarantees_a_minimum_number_of_entries_when_width_filtering_matches_nothing(
        self,
    ) -> None:
        # Zero daily range -> ATR=0 -> width=0 -- filtering by "strike
        # within 0 of spot" matches nothing in practice, so the minimum-
        # entries fallback (closest strikes to spot) must kick in instead
        # of returning an empty chain.
        def handler(request: httpx.Request) -> httpx.Response:
            if "greeks/first_order" in str(request.url):
                return httpx.Response(
                    200,
                    json={
                        "response": [
                            _first_order_entry(strike, "CALL")
                            for strike in ("760.00", "765.00", "769.00", "774.00", "779.00", "784.00", "800.00")
                        ]
                    },
                )
            if "open_interest" in str(request.url):
                return httpx.Response(200, json={"response": []})
            if "interest_rate/history/eod" in str(request.url):
                return httpx.Response(
                    200, json={"response": [{"rate": 3.64, "created": "2026-08-31"}]}
                )
            if "stock/history/eod" in str(request.url):
                return httpx.Response(200, json=_daily_bars_response(daily_range=0.0))
            raise AssertionError(f"unexpected request: {request.url}")

        provider = _provider_with_transport(handler)
        chain = provider.get_option_chain("SPY")

        assert len(chain.contracts) == 6
        strikes = {contract.strike for contract in chain.contracts}
        assert Decimal("800.00") not in strikes  # farthest from spot 769.36 — excluded


class TestRequestConcurrencyLimit:
    def test_limits_concurrent_rest_calls_to_the_documented_account_cap(self) -> None:
        # ThetaData's real, documented Options Standard concurrency cap
        # (2026-09 investigation) — a handler that blocks until released
        # proves no more than this many calls ever run at once, even
        # when far more are requested simultaneously.
        in_flight = 0
        max_observed = 0
        lock = threading.Lock()
        release_event = threading.Event()

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal in_flight, max_observed
            with lock:
                in_flight += 1
                max_observed = max(max_observed, in_flight)
            release_event.wait(timeout=5)
            with lock:
                in_flight -= 1
            return httpx.Response(200, json={"response": []})

        provider = _provider_with_transport(handler)
        thread_count = THETADATA_MAX_CONCURRENT_REQUESTS + 2
        threads = [
            threading.Thread(target=lambda: provider._get_json("/v3/some/path"))
            for _ in range(thread_count)
        ]
        for thread in threads:
            thread.start()

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and in_flight < THETADATA_MAX_CONCURRENT_REQUESTS:
            time.sleep(0.01)
        # Give the two excess threads a moment to prove they stay queued
        # rather than sneaking past the cap.
        time.sleep(0.1)

        try:
            assert in_flight == THETADATA_MAX_CONCURRENT_REQUESTS
            assert max_observed == THETADATA_MAX_CONCURRENT_REQUESTS
        finally:
            release_event.set()
            for thread in threads:
                thread.join(timeout=5)

        assert in_flight == 0


class TestNearTheMoneyCaching:
    def test_get_underlying_snapshot_reuses_get_option_chains_near_the_money_fetch(self) -> None:
        first_order_calls = 0
        open_interest_calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal first_order_calls, open_interest_calls
            if "greeks/first_order" in str(request.url):
                first_order_calls += 1
                return httpx.Response(
                    200, json={"response": [_first_order_entry("769.00", "CALL")]}
                )
            if "open_interest" in str(request.url):
                open_interest_calls += 1
                return httpx.Response(200, json={"response": []})
            if "interest_rate/history/eod" in str(request.url):
                return httpx.Response(
                    200, json={"response": [{"rate": 3.64, "created": "2026-08-31"}]}
                )
            if "stock/history/eod" in str(request.url):
                return httpx.Response(200, json=_daily_bars_response())
            if "stock/snapshot/ohlc" in str(request.url):
                return httpx.Response(200, json={"response": [{"volume": 16396508}]})
            raise AssertionError(f"unexpected request: {request.url}")

        provider = _provider_with_transport(handler)
        provider.get_option_chain("SPY")
        provider.get_underlying_snapshot("SPY")

        # Without the cache this would be 2 and 2 — get_underlying_snapshot
        # requests the exact same near-the-money data get_option_chain
        # already fetched moments earlier in the same refresh cycle.
        assert first_order_calls == 1
        assert open_interest_calls == 1

    def test_cached_data_still_produces_correct_snapshot_values(self) -> None:
        # Same fixture/expected values as
        # TestGetUnderlyingSnapshot.test_approximates_atm_iv_and_pc_oi_ratio_from_near_the_money_chain
        # — proves the cache-hit path is observably identical to the
        # cache-miss (standalone) path, not just fewer requests.
        def handler(request: httpx.Request) -> httpx.Response:
            if "greeks/first_order" in str(request.url):
                return httpx.Response(
                    200,
                    json={
                        "response": [
                            _first_order_entry("768.00", "CALL"),
                            _first_order_entry("769.00", "CALL"),
                        ]
                    },
                )
            if "open_interest" in str(request.url):
                return httpx.Response(
                    200,
                    json={
                        "response": [
                            _open_interest_entry("768.00", "CALL", 400),
                            _open_interest_entry("769.00", "CALL", 600),
                        ]
                    },
                )
            if "interest_rate/history/eod" in str(request.url):
                return httpx.Response(
                    200, json={"response": [{"rate": 3.64, "created": "2026-08-31"}]}
                )
            if "stock/history/eod" in str(request.url):
                return httpx.Response(200, json=_daily_bars_response())
            if "stock/snapshot/ohlc" in str(request.url):
                return httpx.Response(200, json={"response": [{"volume": 16396508}]})
            raise AssertionError(f"unexpected request: {request.url}")

        provider = _provider_with_transport(handler)
        provider.get_option_chain("SPY")
        snapshot = provider.get_underlying_snapshot("SPY")

        assert snapshot.symbol == "SPY"
        assert snapshot.price == Decimal("769.36")
        assert snapshot.volume == 16396508
        assert snapshot.atm_iv == Decimal("0.16")
        assert snapshot.pc_oi_ratio == Decimal(0)
        assert snapshot.skew_25d == Decimal(0)

    def test_cache_does_not_leak_across_symbols(self) -> None:
        calls_by_symbol: dict[str, int] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            if "greeks/first_order" in str(request.url):
                requested_symbol = request.url.params.get("symbol")
                calls_by_symbol[requested_symbol] = calls_by_symbol.get(requested_symbol, 0) + 1
                return httpx.Response(
                    200,
                    json={
                        "response": [
                            _first_order_entry("769.00", "CALL", underlying_price="769.36")
                        ]
                    },
                )
            if "open_interest" in str(request.url):
                return httpx.Response(200, json={"response": []})
            if "interest_rate/history/eod" in str(request.url):
                return httpx.Response(
                    200, json={"response": [{"rate": 3.64, "created": "2026-08-31"}]}
                )
            if "stock/history/eod" in str(request.url):
                return httpx.Response(200, json=_daily_bars_response())
            raise AssertionError(f"unexpected request: {request.url}")

        provider = _provider_with_transport(handler)
        provider.get_option_chain("SPY")
        provider.get_option_chain("QQQ")

        assert calls_by_symbol == {"SPY": 1, "QQQ": 1}
