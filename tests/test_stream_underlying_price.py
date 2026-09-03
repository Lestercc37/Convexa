from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from backend.domain.entities import MarketPrice, UnderlyingTradeEvent
from backend.domain.use_cases.stream_underlying_price import StreamUnderlyingPriceUseCase

NOW = datetime(2026, 1, 15, 14, 30, tzinfo=UTC)


class _FakeStorage:
    def __init__(self) -> None:
        self.saved: list[MarketPrice] = []

    def save_market_price(self, price: MarketPrice) -> None:
        self.saved.append(price)


class _FakeProvider:
    """A finite, deterministic stand-in for IDataProvider.stream_underlying_trades
    — replays a fixed list, same convention as _FakeProvider in
    test_stream_whale_alerts.py."""

    def __init__(self, trades: list[UnderlyingTradeEvent] | None = None) -> None:
        self._trades = trades or []

    async def stream_underlying_trades(self, underlying: str) -> AsyncIterator[UnderlyingTradeEvent]:
        for trade in self._trades:
            yield trade


def _trade(price: str, symbol: str = "SPY", as_of: datetime = NOW, size: int = 100) -> UnderlyingTradeEvent:
    return UnderlyingTradeEvent(symbol=symbol, as_of=as_of, price=Decimal(price), size=size)


@pytest.mark.asyncio
async def test_a_trade_persists_a_market_price() -> None:
    storage = _FakeStorage()
    provider = _FakeProvider(trades=[_trade("552.25")])
    use_case = StreamUnderlyingPriceUseCase(provider, storage)

    await use_case.run("SPY")

    assert len(storage.saved) == 1
    saved = storage.saved[0]
    assert saved.symbol == "SPY"
    assert saved.price == Decimal("552.25")
    assert saved.as_of == NOW
    # Matches ThetaDataProvider's own documented volume=0 gap (no live
    # Stocks/Indices share-volume subscription) -- not a regression.
    assert saved.volume == 0


@pytest.mark.asyncio
async def test_ticks_within_the_debounce_window_are_not_all_persisted() -> None:
    storage = _FakeStorage()
    provider = _FakeProvider(
        trades=[_trade("552.25"), _trade("552.30"), _trade("552.35")],
    )
    # A large window so all 3 fixture trades (processed effectively
    # instantly) fall inside it.
    use_case = StreamUnderlyingPriceUseCase(provider, storage, min_write_interval_seconds=60.0)

    await use_case.run("SPY")

    assert len(storage.saved) == 1
    assert storage.saved[0].price == Decimal("552.25")  # only the first tick


@pytest.mark.asyncio
async def test_a_tick_after_the_debounce_window_elapses_is_persisted() -> None:
    storage = _FakeStorage()
    provider = _FakeProvider(trades=[_trade("552.25"), _trade("552.30")])
    # A window of 0 -- every tick is past "the last write", regardless
    # of real elapsed time -- isolates the test from real wall-clock
    # timing flakiness while still exercising the same code path.
    use_case = StreamUnderlyingPriceUseCase(provider, storage, min_write_interval_seconds=0.0)

    await use_case.run("SPY")

    assert [price.price for price in storage.saved] == [Decimal("552.25"), Decimal("552.30")]


@pytest.mark.asyncio
async def test_debounce_is_tracked_independently_per_symbol() -> None:
    storage = _FakeStorage()
    provider = _FakeProvider(trades=[_trade("552.25", symbol="SPY"), _trade("470.10", symbol="QQQ")])
    use_case = StreamUnderlyingPriceUseCase(provider, storage, min_write_interval_seconds=60.0)

    await use_case.run("SPY")

    symbols = {price.symbol for price in storage.saved}
    assert symbols == {"SPY", "QQQ"}


@pytest.mark.asyncio
async def test_run_completes_immediately_for_a_provider_with_nothing_to_stream() -> None:
    storage = _FakeStorage()
    provider = _FakeProvider()  # no trades -- same shape as MockDataProvider's no-op

    await asyncio.wait_for(
        StreamUnderlyingPriceUseCase(provider, storage).run("SPY"), timeout=1
    )

    assert storage.saved == []
