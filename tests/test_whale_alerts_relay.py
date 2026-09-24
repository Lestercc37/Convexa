from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
import pytest_asyncio

from backend.core.whale_alerts_relay import (
    RECONNECT_BASE_DELAY_SECONDS,
    RELAY_QUEUE_MAXSIZE,
    RelayDataProvider,
    WhaleAlertsRelayPublisher,
    WhaleAlertsRelayServer,
)
from backend.domain.entities import FlowEvent, FlowEventType, QuoteEvent, Side


def _trade(symbol: str = "SPY", occ_symbol: str = "SPY260101C00500000") -> FlowEvent:
    return FlowEvent(
        symbol=symbol,
        occ_symbol=occ_symbol,
        as_of=datetime(2026, 9, 24, 14, 30, tzinfo=UTC),
        event_type=FlowEventType.SWEEP,
        premium=Decimal("12345.67"),
        size=10,
        aggressor_side=Side.BUY,
    )


def _quote(symbol: str = "SPY", occ_symbol: str = "SPY260101C00500000") -> QuoteEvent:
    return QuoteEvent(
        symbol=symbol,
        occ_symbol=occ_symbol,
        as_of=datetime(2026, 9, 24, 14, 30, tzinfo=UTC),
        bid=Decimal("1.20"),
        ask=Decimal("1.25"),
    )


class _FakeStreamingProvider:
    """A minimal fake satisfying only what WhaleAlertsRelayPublisher
    calls -- an already-exhausted async generator per symbol, same shape
    MockDataProvider's own stream_trades/stream_quotes use, so the
    publisher's per-symbol task completes cleanly instead of hanging a
    test."""

    def __init__(
        self, trades: dict[str, list[FlowEvent]], quotes: dict[str, list[QuoteEvent]]
    ) -> None:
        self._trades = trades
        self._quotes = quotes

    async def stream_trades(self, underlying: str) -> AsyncIterator[FlowEvent]:
        for event in self._trades.get(underlying.upper(), []):
            yield event

    async def stream_quotes(self, underlying: str) -> AsyncIterator[QuoteEvent]:
        for event in self._quotes.get(underlying.upper(), []):
            yield event


@pytest_asyncio.fixture
async def relay_pair() -> AsyncIterator[tuple[WhaleAlertsRelayServer, RelayDataProvider]]:
    server = WhaleAlertsRelayServer("127.0.0.1", 0)
    await server.start()
    client = RelayDataProvider("127.0.0.1", server.port)
    await client.start()
    # Give the client's connect task a moment to actually establish --
    # publishing before a client is connected is a defined no-op (see
    # test_publish_with_no_client_connected_is_a_silent_no_op below), not
    # a bug, but every OTHER test here wants a real connection first.
    for _ in range(50):
        if server._clients:
            break
        await asyncio.sleep(0.02)
    try:
        yield server, client
    finally:
        await client.stop()
        await server.stop()


class TestRoundTrip:
    @pytest.mark.asyncio
    async def test_a_published_trade_is_received_intact_on_the_other_end(
        self, relay_pair: tuple[WhaleAlertsRelayServer, RelayDataProvider]
    ) -> None:
        server, client = relay_pair
        trade = _trade()
        server.publish_trade(trade)

        received = await asyncio.wait_for(client.stream_trades("SPY").__anext__(), timeout=2)

        assert received == trade

    @pytest.mark.asyncio
    async def test_a_published_quote_is_received_intact_on_the_other_end(
        self, relay_pair: tuple[WhaleAlertsRelayServer, RelayDataProvider]
    ) -> None:
        server, client = relay_pair
        quote = _quote()
        server.publish_quote(quote)

        received = await asyncio.wait_for(client.stream_quotes("SPY").__anext__(), timeout=2)

        assert received == quote

    @pytest.mark.asyncio
    async def test_events_for_different_symbols_land_in_separate_streams(
        self, relay_pair: tuple[WhaleAlertsRelayServer, RelayDataProvider]
    ) -> None:
        server, client = relay_pair
        spy_trade = _trade(symbol="SPY", occ_symbol="SPY260101C00500000")
        qqq_trade = _trade(symbol="QQQ", occ_symbol="QQQ260101C00400000")
        server.publish_trade(spy_trade)
        server.publish_trade(qqq_trade)

        received_spy = await asyncio.wait_for(client.stream_trades("SPY").__anext__(), timeout=2)
        received_qqq = await asyncio.wait_for(client.stream_trades("QQQ").__anext__(), timeout=2)

        assert received_spy == spy_trade
        assert received_qqq == qqq_trade

    @pytest.mark.asyncio
    async def test_a_large_burst_arrives_without_drops_or_reordering(
        self, relay_pair: tuple[WhaleAlertsRelayServer, RelayDataProvider]
    ) -> None:
        # Confirmed live, 2026-09-24: draining the outbound queue one
        # writer.drain() round-trip per message (rather than batching
        # everything queued before draining once -- see _ConnectedClient
        # ._drain's own comment) couldn't keep up with real trade+quote
        # volume across 15 symbols, dropping 2M+ messages in under 20
        # minutes even though nothing downstream was genuinely falling
        # behind. This burst (comfortably within RELAY_QUEUE_MAXSIZE, so
        # a genuine backpressure drop -- covered separately, in
        # TestBackpressure -- isn't what would fail this test) is the
        # regression test for that fix: every message must still arrive,
        # in order, quickly.
        server, client = relay_pair
        count = 5000
        trades = [_trade(occ_symbol=f"SPY260101C00{500 + i:03d}000") for i in range(count)]
        for trade in trades:
            server.publish_trade(trade)

        received: list[FlowEvent] = []
        stream = client.stream_trades("SPY").__aiter__()
        async with asyncio.timeout(15):
            while len(received) < count:
                received.append(await stream.__anext__())

        assert received == trades


class TestBackpressure:
    def test_publish_with_no_client_connected_is_a_silent_no_op(self) -> None:
        # No event loop needed -- publish_trade/publish_quote never
        # awaits anything, so this stays a plain sync test.
        server = WhaleAlertsRelayServer("127.0.0.1", 0)
        server.publish_trade(_trade())  # must not raise
        server.publish_quote(_quote())  # must not raise

    @pytest.mark.asyncio
    async def test_a_full_client_queue_drops_and_logs_critical_instead_of_blocking(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        client = RelayDataProvider("127.0.0.1", 0)
        queue: asyncio.Queue[FlowEvent] = asyncio.Queue(maxsize=1)
        queue.put_nowait(_trade())  # pre-fill to capacity
        client._trade_queues["SPY"] = queue

        with caplog.at_level(logging.CRITICAL):
            client._dispatch(
                {
                    "k": "t",
                    "symbol": "SPY",
                    "occ_symbol": "SPY260101C00500000",
                    "as_of": "2026-09-24T14:30:00+00:00",
                    "event_type": "sweep",
                    "premium": "1",
                    "size": 1,
                    "aggressor_side": "buy",
                }
            )  # must not raise

        assert any("queue for SPY is full" in record.message for record in caplog.records)


class TestRelayDataProviderUnsupportedMethods:
    def test_non_streaming_methods_raise_not_implemented(self) -> None:
        client = RelayDataProvider("127.0.0.1", 0)
        with pytest.raises(NotImplementedError):
            client.get_option_chain("SPY")
        with pytest.raises(NotImplementedError):
            client.get_underlying_snapshot("SPY")
        with pytest.raises(NotImplementedError):
            client.get_daily_bars("SPY")
        with pytest.raises(NotImplementedError):
            client.get_market_holidays(2026)

    @pytest.mark.asyncio
    async def test_stream_underlying_trades_raises_not_implemented(self) -> None:
        client = RelayDataProvider("127.0.0.1", 0)
        with pytest.raises(NotImplementedError):
            await client.stream_underlying_trades("SPY").__anext__()

    def test_cumulative_volumes_is_empty_not_a_stub_error(self) -> None:
        # Matches IDataProvider's own documented contract for a provider
        # whose stream was never started -- this adapter genuinely never
        # tracks this itself (see the method's own docstring), so {} is
        # correct, unlike every other method above.
        client = RelayDataProvider("127.0.0.1", 0)
        assert client.cumulative_volumes() == {}


class TestWhaleAlertsRelayPublisher:
    @pytest.mark.asyncio
    async def test_forwards_every_symbols_trades_and_quotes_to_the_relay(self) -> None:
        provider = _FakeStreamingProvider(
            trades={"SPY": [_trade()], "QQQ": []},
            quotes={"SPY": [_quote()], "QQQ": []},
        )
        published_trades: list[FlowEvent] = []
        published_quotes: list[QuoteEvent] = []

        class _RecordingRelay:
            def publish_trade(self, event: FlowEvent) -> None:
                published_trades.append(event)

            def publish_quote(self, event: QuoteEvent) -> None:
                published_quotes.append(event)

        publisher = WhaleAlertsRelayPublisher(provider, _RecordingRelay())  # type: ignore[arg-type]
        await publisher._run_symbol("SPY")

        assert published_trades == [_trade()]
        assert published_quotes == [_quote()]

    @pytest.mark.asyncio
    async def test_start_and_stop_manage_one_task_per_active_symbol(self) -> None:
        provider = _FakeStreamingProvider(trades={}, quotes={})

        class _NoopRelay:
            def publish_trade(self, event: FlowEvent) -> None:
                pass

            def publish_quote(self, event: QuoteEvent) -> None:
                pass

        publisher = WhaleAlertsRelayPublisher(provider, _NoopRelay())  # type: ignore[arg-type]
        publisher.start()
        assert len(publisher._tasks) > 0

        # A second start() while already running is a no-op, same
        # contract as every other manager in this codebase.
        tasks_before = list(publisher._tasks)
        publisher.start()
        assert publisher._tasks == tasks_before

        await publisher.stop()
        assert publisher._tasks == []


def test_reconnect_backoff_constants_match_the_rest_of_the_codebase() -> None:
    # Deliberately the same numbers as ThetaStreamHub/WhaleAlertsStream
    # Manager/PriceNotificationListener -- not asserting a specific
    # value in isolation, just that nobody quietly drifted this module
    # away from the shared convention.
    assert RECONNECT_BASE_DELAY_SECONDS == 2
    assert RELAY_QUEUE_MAXSIZE > 0
