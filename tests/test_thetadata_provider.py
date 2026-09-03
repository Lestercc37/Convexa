from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from datetime import date, datetime, timedelta
from decimal import Decimal

import httpx
import pytest

from backend.adapters.providers.thetadata.provider import (
    THETADATA_MAX_CONCURRENT_REQUESTS,
    ThetaDataProvider,
    ThetaQuoteStream,
    ThetaTradeStream,
    ThetaUnderlyingTradeStream,
    _build_occ_symbol,
    _log_req_response,
    _parse_et_timestamp,
    _time_to_expiration_years,
)
from backend.domain.entities import ContractType, UnderlyingKind
from backend.domain.use_cases.market_hours import EASTERN_TIME

REST_URL = "http://thetaterminal.test"
WS_URL = "ws://thetaterminal.test/v1/events"


def _first_order_entry(
    strike: str, right: str, expiration: str = "2026-09-18", underlying_price: str = "769.36"
) -> dict[str, object]:
    return {
        "contract": {"symbol": "SPY", "expiration": expiration, "right": right, "strike": float(strike)},
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


def _open_interest_entry(strike: str, right: str, oi: int, expiration: str = "2026-09-18") -> dict[str, object]:
    return {
        "contract": {"symbol": "SPY", "expiration": expiration, "right": right, "strike": float(strike)},
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
    provider._stream = ThetaTradeStream(WS_URL, provider._client)
    return provider


class TestHelpers:
    def test_build_occ_symbol_matches_mock_provider_pattern(self) -> None:
        occ = _build_occ_symbol("SPY", date(2026, 9, 18), ContractType.CALL, Decimal("770"))
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
        provider._stream.register_contract(
            occ_call, "SPY", date(2026, 9, 18), ContractType.CALL, Decimal("769.00")
        )
        provider._stream._cumulative_volume[occ_call] = 4242

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

    def test_all_three_stream_classes_route_req_response_through_the_shared_helper(
        self,
    ) -> None:
        """Confirms the elif branch exists in all 3 _connect_and_consume
        methods (not just that _log_req_response itself works) -- reads
        the compiled source directly rather than driving a full
        websocket loop, matching this file's own convention of testing
        _handle_trade/_handle_quote directly instead of the recv() loop
        around them."""
        import inspect

        for stream_class in (ThetaTradeStream, ThetaQuoteStream, ThetaUnderlyingTradeStream):
            source = inspect.getsource(stream_class._connect_and_consume)
            assert '"REQ_RESPONSE"' in source
            assert "_log_req_response" in source


class TestTradeStream:
    def test_handle_trade_accumulates_cumulative_volume(self) -> None:
        stream = ThetaTradeStream(WS_URL, httpx.Client(base_url=REST_URL))
        message = {
            "header": {"type": "TRADE", "status": "CONNECTED"},
            "contract": {"root": "SPY", "expiration": 20260918, "strike": 770000, "right": "C"},
            "trade": {"size": 15, "price": 1.09, "sequence": 12345},
        }

        stream._handle_trade(message)
        stream._handle_trade(message)

        occ = _build_occ_symbol("SPY", date(2026, 9, 18), ContractType.CALL, Decimal("770"))
        assert stream.cumulative_volume(occ) == 30

    @pytest.mark.asyncio
    async def test_handle_trade_publishes_a_flow_event_to_subscribers(self) -> None:
        stream = ThetaTradeStream(WS_URL, httpx.Client(base_url=REST_URL))
        queue = stream.subscribe_queue("SPY")
        message = {
            "header": {"type": "TRADE", "status": "CONNECTED"},
            "contract": {"root": "SPY", "expiration": 20260918, "strike": 770000, "right": "C"},
            "trade": {"size": 10, "price": 2.00, "sequence": 1},
        }

        stream._handle_trade(message)
        event = await asyncio.wait_for(queue.get(), timeout=1)

        assert event.symbol == "SPY"
        assert event.size == 10
        assert event.premium == Decimal("2.00") * Decimal(10) * Decimal(100)

    def test_handle_trade_ignores_incomplete_messages(self) -> None:
        stream = ThetaTradeStream(WS_URL, httpx.Client(base_url=REST_URL))
        stream._handle_trade({"contract": {"root": "SPY"}, "trade": {}})
        # No exception, no volume recorded anywhere.
        assert stream._cumulative_volume == {}

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
        stream = ThetaTradeStream(WS_URL, client)
        stream.register_contract(
            "SPY260918C00770000", "SPY", date(2026, 9, 18), ContractType.CALL, Decimal("770")
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
        stream = ThetaTradeStream(WS_URL, client)
        stream.register_contract(
            "SPY260918C00770000", "SPY", date(2026, 9, 18), ContractType.CALL, Decimal("770")
        )
        stream._cumulative_volume["SPY260918C00770000"] = 995

        with caplog.at_level(logging.INFO):
            stream._reconcile()

        assert any("reconciled" in record.message for record in caplog.records)
        assert not any(record.levelno == logging.WARNING for record in caplog.records)

    @pytest.mark.asyncio
    async def test_run_backs_off_exponentially_between_reconnect_attempts(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stream = ThetaTradeStream(WS_URL, httpx.Client(base_url=REST_URL))
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


class TestQuoteStream:
    def test_handle_quote_publishes_a_quote_event_to_subscribers(self) -> None:
        stream = ThetaQuoteStream(WS_URL)
        queue = stream.subscribe_queue("SPY")
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

        occ = _build_occ_symbol("SPY", date(2026, 9, 18), ContractType.CALL, Decimal("770"))
        assert event.symbol == "SPY"
        assert event.occ_symbol == occ
        assert event.bid == Decimal("1.08")
        assert event.ask == Decimal("1.09")

    def test_handle_quote_ignores_incomplete_messages(self) -> None:
        stream = ThetaQuoteStream(WS_URL)
        queue = stream.subscribe_queue("SPY")

        stream._handle_quote({"contract": {"root": "SPY"}, "quote": {"bid": 1.08}})

        assert queue.empty()

    @pytest.mark.asyncio
    async def test_run_backs_off_exponentially_between_reconnect_attempts(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stream = ThetaQuoteStream(WS_URL)
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
    async def test_start_and_stop_manage_a_single_background_task(self) -> None:
        stream = ThetaQuoteStream(WS_URL)
        stream.start()
        task = stream._task
        assert task is not None

        # A second start() while already running is a no-op — same
        # contract as ThetaTradeStream.start().
        stream.start()
        assert stream._task is task

        await stream.stop()
        assert stream._task is None


class TestUnderlyingTradeStream:
    """Fixtures below were built from ThetaData's public v3 docs
    (https://docs.thetadata.us/Streaming/US-Stocks/Trade-Stream.html)
    and confirmed live against a real Theta Terminal, market open
    (2026-09-03, Stocks+Index plans active): SPY/TSLA (STOCK) and
    SPX/VIX/NDX (INDEX) all delivered genuine TRADE messages in exactly
    this shape, size=0 confirmed for every INDEX message. REQ_RESPONSE
    handling was confirmed live too — all 5 subscriptions logged
    "SUBSCRIBED" at debug via the shared _log_req_response helper.

    A real production bug was found and fixed here (2026-09-03): the
    local Theta Terminal broadcasts every symbol/contract with an active
    subscription ANYWHERE on that Terminal to EVERY connected WebSocket
    client, not just what a given connection itself subscribed to. A
    connection that only asked for `sec_type: "INDEX"` on "VIX" still
    received `security_type: "OPTION"` trade messages for the same root
    (leaking in from this same backend's own ThetaTradeStream, which was
    separately subscribed to VIX's near-the-money option chain) —
    `_handle_trade` used to only check `contract.root`, so those got
    published as if they were VIX's own price. Quantified live before
    the fix: 60% of root="VIX" messages over 60s were OPTION
    contamination (9 of 15), ~9% for root="SPX" (13 of 139).
    `_handle_trade` now also checks `contract.security_type` against the
    registered `UnderlyingKind` — see
    test_option_trades_sharing_the_same_root_are_filtered_out and its
    neighbors below, which also register a symbol/kind before calling
    `_handle_trade` for exactly this reason (an unregistered symbol has
    no expected `security_type` to validate against, so it's dropped —
    see test_trade_for_an_unregistered_symbol_is_dropped_not_guessed)."""

    def test_handle_trade_publishes_an_underlying_trade_event_to_subscribers(self) -> None:
        stream = ThetaUnderlyingTradeStream(WS_URL)
        stream.register_symbol("AAPL", UnderlyingKind.EQUITY)
        queue = stream.subscribe_queue("AAPL")
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

        stream._handle_trade(message)
        event = queue.get_nowait()

        assert event.symbol == "AAPL"
        assert event.price == Decimal("184.5099")
        assert event.size == 500

    def test_handle_trade_only_publishes_to_the_matching_symbols_subscribers(self) -> None:
        stream = ThetaUnderlyingTradeStream(WS_URL)
        stream.register_symbol("AAPL", UnderlyingKind.EQUITY)
        aapl_queue = stream.subscribe_queue("AAPL")
        spy_queue = stream.subscribe_queue("SPY")
        message = {
            "header": {"type": "TRADE", "status": "CONNECTED"},
            "contract": {"security_type": "STOCK", "root": "AAPL"},
            "trade": {"size": 1, "price": 100.0},
        }

        stream._handle_trade(message)

        assert not aapl_queue.empty()
        assert spy_queue.empty()

    def test_option_trades_sharing_the_same_root_are_filtered_out(self) -> None:
        """Regression test for a real production bug (confirmed live,
        2026-09-03 market open, see this class's own docstring): before
        the fix, _handle_trade only checked contract.root, so this exact
        VIX call option trade (captured live) was accepted and published
        as if it were VIX's own price ($1.57 published as "VIX price",
        real VIX index level ~14.87-14.89 at the same moment). Now
        _handle_trade also checks contract.security_type against the
        registered UnderlyingKind, so an OPTION trade sharing the root
        must be dropped instead."""
        stream = ThetaUnderlyingTradeStream(WS_URL)
        stream.register_symbol("VIX", UnderlyingKind.INDEX)
        queue = stream.subscribe_queue("VIX")
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

        stream._handle_trade(message)

        assert queue.empty()

    def test_genuine_index_trade_for_a_registered_symbol_still_publishes(self) -> None:
        """The fix must not be overly broad -- a genuine INDEX trade for
        VIX (security_type matching the registered UnderlyingKind.INDEX)
        must still publish normally. Exact shape captured live alongside
        the option-contamination messages above."""
        stream = ThetaUnderlyingTradeStream(WS_URL)
        stream.register_symbol("VIX", UnderlyingKind.INDEX)
        queue = stream.subscribe_queue("VIX")
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

        stream._handle_trade(message)
        event = queue.get_nowait()

        assert event.symbol == "VIX"
        assert event.price == Decimal("14.88")

    def test_option_trade_for_an_equity_root_is_also_filtered_out(self) -> None:
        """Same fix, STOCK side -- confirmed live that root="SPX" (an
        INDEX) also saw OPTION contamination (13 of 139 messages over
        60s), and the same leak mechanism applies to any registered
        EQUITY symbol whose options are subscribed elsewhere on the
        same Terminal."""
        stream = ThetaUnderlyingTradeStream(WS_URL)
        stream.register_symbol("SPY", UnderlyingKind.EQUITY)
        queue = stream.subscribe_queue("SPY")
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

        stream._handle_trade(message)

        assert queue.empty()

    def test_trade_for_an_unregistered_symbol_is_dropped_not_guessed(self) -> None:
        """No registered UnderlyingKind means no expected security_type
        to validate against -- dropping is the safe default, not a
        guess. Harmless either way (no subscriber would exist for an
        unregistered symbol), but explicit is better than relying on
        that coincidence."""
        stream = ThetaUnderlyingTradeStream(WS_URL)
        queue = stream.subscribe_queue("QQQ")
        message = {
            "header": {"type": "TRADE", "status": "CONNECTED"},
            "contract": {"security_type": "STOCK", "root": "QQQ"},
            "trade": {"size": 10, "price": 500.0},
        }

        stream._handle_trade(message)

        assert queue.empty()

    def test_handle_trade_ignores_incomplete_messages(self) -> None:
        stream = ThetaUnderlyingTradeStream(WS_URL)
        queue = stream.subscribe_queue("AAPL")

        stream._handle_trade({"contract": {"root": "AAPL"}, "trade": {"size": 500}})  # no price

        assert queue.empty()

    def test_subscribe_sends_the_documented_stock_trade_stream_payload(self) -> None:
        stream = ThetaUnderlyingTradeStream(WS_URL)
        sent: list[str] = []

        class _FakeWebSocket:
            async def send(self, payload: str) -> None:
                sent.append(payload)

        asyncio.run(stream._subscribe(_FakeWebSocket(), "AAPL", UnderlyingKind.EQUITY))

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

    def test_subscribe_uses_sec_type_index_for_index_underlyings(self) -> None:
        # Confirmed from ThetaData's docs: indices use a genuinely
        # separate stream (US-Indices Price Stream, its own "Index
        # Standard" subscription) — sec_type is the only field that
        # differs from the stock variant, the trade message shape itself
        # is identical.
        stream = ThetaUnderlyingTradeStream(WS_URL)
        sent: list[str] = []

        class _FakeWebSocket:
            async def send(self, payload: str) -> None:
                sent.append(payload)

        asyncio.run(stream._subscribe(_FakeWebSocket(), "SPX", UnderlyingKind.INDEX))

        payload = json.loads(sent[0])
        assert payload["sec_type"] == "INDEX"
        assert payload["contract"] == {"root": "SPX"}

    @pytest.mark.asyncio
    async def test_run_backs_off_exponentially_between_reconnect_attempts(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stream = ThetaUnderlyingTradeStream(WS_URL)
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
    async def test_start_and_stop_manage_a_single_background_task(self) -> None:
        stream = ThetaUnderlyingTradeStream(WS_URL)
        stream.start()
        task = stream._task
        assert task is not None

        stream.start()  # a second start() while running is a no-op
        assert stream._task is task

        await stream.stop()
        assert stream._task is None


class TestProviderLifecycle:
    @pytest.mark.asyncio
    async def test_stop_closes_the_rest_client_and_stream(self) -> None:
        provider = ThetaDataProvider(REST_URL, WS_URL)
        await provider.stop()
        assert provider._client.is_closed
        assert provider._stream._task is None
        assert provider._quote_stream._task is None
        assert provider._underlying_trade_stream._task is None

    @pytest.mark.asyncio
    async def test_stream_underlying_trades_yields_events_from_the_queue(self) -> None:
        provider = ThetaDataProvider(REST_URL, WS_URL)
        provider._underlying_trade_stream.register_symbol("SPY", UnderlyingKind.EQUITY)
        events = provider.stream_underlying_trades("SPY")
        # Advance the async generator to its subscribe_queue() + first
        # `await queue.get()` before publishing — otherwise the event
        # below would be put_nowait'd to a queue nothing has subscribed
        # to yet and silently dropped.
        pending = asyncio.ensure_future(events.__anext__())
        await asyncio.sleep(0)

        message = {
            "header": {"type": "TRADE", "status": "CONNECTED"},
            "contract": {"security_type": "STOCK", "root": "SPY"},
            "trade": {"size": 100, "price": 552.25},
        }
        provider._underlying_trade_stream._handle_trade(message)
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

            registered = provider._underlying_trade_stream._symbols
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
