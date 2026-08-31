from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from decimal import Decimal

import httpx
import pytest

from backend.adapters.providers.thetadata.provider import (
    ThetaDataProvider,
    ThetaTradeStream,
    _build_occ_symbol,
    _parse_et_timestamp,
    _time_to_expiration_years,
)
from backend.domain.entities import ContractType
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
            raise AssertionError(f"unexpected request: {request.url}")

        provider = _provider_with_transport(handler)
        snapshot = provider.get_underlying_snapshot("SPY")

        assert snapshot.symbol == "SPY"
        assert snapshot.price == Decimal("769.36")
        # Documented limitation: no live Stocks/Indices subscription ->
        # no source anywhere for the underlying's own share volume.
        assert snapshot.volume == 0
        assert snapshot.atm_iv == Decimal("0.16")
        # All open interest sampled above is CALL-side (no puts in this
        # response) -> put_oi is 0 -> documented pc_oi_ratio fallback.
        assert snapshot.pc_oi_ratio == Decimal(0)
        # No 25-delta strikes fetched -> documented as 0, not guessed.
        assert snapshot.skew_25d == Decimal(0)


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


class TestProviderLifecycle:
    @pytest.mark.asyncio
    async def test_stop_closes_the_rest_client_and_stream(self) -> None:
        provider = ThetaDataProvider(REST_URL, WS_URL)
        await provider.stop()
        assert provider._client.is_closed
