from __future__ import annotations

from decimal import Decimal

from backend.adapters.providers.thetadata.stream_parsing import (
    parse_option_trade_message,
    parse_quote_message,
    parse_underlying_trade_message,
)
from backend.domain.entities import UnderlyingKind


class TestParseQuoteMessage:
    def test_parses_a_real_shaped_quote(self) -> None:
        parsed = parse_quote_message(
            {
                "contract": {
                    "root": "SPY",
                    "expiration": 20260918,
                    "strike": 770000,
                    "right": "C",
                },
                "quote": {"bid": 1.08, "ask": 1.09, "date": 20261002, "ms_of_day": 44726780},
            }
        )
        assert parsed is not None
        assert parsed.event.bid == Decimal("1.08")
        assert parsed.event.ask == Decimal("1.09")
        assert parsed.underlying_symbol == "SPY"
        assert parsed.exchange_ts is not None

    def test_returns_none_when_a_required_field_is_missing(self) -> None:
        assert parse_quote_message({"contract": {"root": "SPY"}, "quote": {"bid": 1.0}}) is None

    def test_exchange_ts_is_none_without_date_or_ms_of_day(self) -> None:
        parsed = parse_quote_message(
            {
                "contract": {
                    "root": "SPY",
                    "expiration": 20260918,
                    "strike": 770000,
                    "right": "C",
                },
                "quote": {"bid": 1.08, "ask": 1.09},
            }
        )
        assert parsed is not None
        assert parsed.exchange_ts is None


class TestParseOptionTradeMessage:
    def test_parses_a_real_shaped_trade_with_price(self) -> None:
        parsed = parse_option_trade_message(
            {
                "contract": {
                    "root": "SPY",
                    "expiration": 20260918,
                    "strike": 770000,
                    "right": "P",
                },
                "trade": {"size": 10, "price": 1.42, "date": 20261002, "ms_of_day": 44726778},
            }
        )
        assert parsed is not None
        assert parsed.size == 10
        assert parsed.event is not None
        assert parsed.event.premium == Decimal("1.42") * Decimal(10) * Decimal(100)

    def test_a_trade_with_no_price_still_reports_size_with_no_event(self) -> None:
        parsed = parse_option_trade_message(
            {
                "contract": {
                    "root": "SPY",
                    "expiration": 20260918,
                    "strike": 770000,
                    "right": "P",
                },
                "trade": {"size": 10},
            }
        )
        assert parsed is not None
        assert parsed.size == 10
        assert parsed.event is None


class TestParseUnderlyingTradeMessage:
    def test_parses_a_real_shaped_underlying_trade(self) -> None:
        parsed = parse_underlying_trade_message(
            {
                "contract": {"security_type": "STOCK", "root": "AAPL"},
                "trade": {"size": 500, "price": 184.51},
            },
            symbols={"AAPL": UnderlyingKind.EQUITY},
        )
        assert parsed is not None
        assert parsed.event.price == Decimal("184.51")
        assert parsed.event.size == 500

    def test_an_unregistered_symbol_is_dropped(self) -> None:
        parsed = parse_underlying_trade_message(
            {
                "contract": {"security_type": "STOCK", "root": "AAPL"},
                "trade": {"size": 500, "price": 184.51},
            },
            symbols={},
        )
        assert parsed is None

    def test_a_security_type_mismatch_is_dropped(self) -> None:
        # The 2026-09-03 incident this guard fixed -- an OPTION trade
        # sharing a root leaking in and getting published as the
        # underlying's own price.
        parsed = parse_underlying_trade_message(
            {
                "contract": {"security_type": "OPTION", "root": "VIX"},
                "trade": {"size": 1, "price": 0.45},
            },
            symbols={"VIX": UnderlyingKind.INDEX},
        )
        assert parsed is None


