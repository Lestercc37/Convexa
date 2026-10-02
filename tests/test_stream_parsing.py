from __future__ import annotations

from datetime import date
from decimal import Decimal

from backend.adapters.providers.thetadata.stream_parsing import (
    ParsedOptionTrade,
    ParsedQuote,
    ParsedUnderlyingTrade,
    decode_parsed_event,
    encode_parsed_event,
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


class TestEncodeDecodeRoundTrip:
    """encode_parsed_event/decode_parsed_event -- the wire format
    StreamProcessorRelayClient sends a classified result back to
    worker.py in. A round-trip must reproduce every field the receiving
    side (ThetaStreamHub._handle_processed_event) actually reads."""

    def test_quote_round_trips(self) -> None:
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

        decoded = decode_parsed_event(encode_parsed_event(parsed))

        assert isinstance(decoded, ParsedQuote)
        assert decoded.event.bid == parsed.event.bid
        assert decoded.event.ask == parsed.event.ask
        assert decoded.event.occ_symbol == parsed.event.occ_symbol
        assert decoded.underlying_symbol == parsed.underlying_symbol
        assert decoded.exchange_ts == parsed.exchange_ts

    def test_option_trade_with_an_event_round_trips(self) -> None:
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

        decoded = decode_parsed_event(encode_parsed_event(parsed))

        assert isinstance(decoded, ParsedOptionTrade)
        assert decoded.occ_symbol == parsed.occ_symbol
        assert decoded.size == parsed.size
        assert decoded.event is not None
        assert decoded.event.premium == parsed.event.premium

    def test_option_trade_without_an_event_round_trips_with_event_none(self) -> None:
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

        decoded = decode_parsed_event(encode_parsed_event(parsed))

        assert isinstance(decoded, ParsedOptionTrade)
        assert decoded.event is None
        assert decoded.size == 10

    def test_underlying_trade_round_trips(self) -> None:
        parsed = parse_underlying_trade_message(
            {
                "contract": {"security_type": "STOCK", "root": "AAPL"},
                "trade": {"size": 500, "price": 184.51},
            },
            symbols={"AAPL": UnderlyingKind.EQUITY},
        )
        assert parsed is not None

        decoded = decode_parsed_event(encode_parsed_event(parsed))

        assert isinstance(decoded, ParsedUnderlyingTrade)
        assert decoded.event.symbol == parsed.event.symbol
        assert decoded.event.price == parsed.event.price
        assert decoded.event.size == parsed.event.size

    def test_none_exchange_ts_round_trips_as_none(self) -> None:
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

        decoded = decode_parsed_event(encode_parsed_event(parsed))

        assert decoded.exchange_ts is None
