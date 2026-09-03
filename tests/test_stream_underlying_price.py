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
    def __init__(self, seed: MarketPrice | None = None) -> None:
        self.saved: list[MarketPrice] = []
        self._latest: dict[str, MarketPrice] = {seed.symbol: seed} if seed is not None else {}

    def save_market_price(self, price: MarketPrice) -> None:
        self.saved.append(price)
        self._latest[price.symbol] = price

    def get_latest_price(self, underlying: str) -> MarketPrice | None:
        return self._latest.get(underlying.upper())


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
    # No prior snapshot exists in storage for this symbol yet -- same
    # starting value this field already had, not a regression.
    assert saved.volume == 0


@pytest.mark.asyncio
async def test_a_trade_carries_forward_the_volume_already_in_storage() -> None:
    """The REST scheduler (RefreshUnderlyingSnapshotUseCase ->
    ThetaDataProvider.get_underlying_snapshot) is the only thing that
    ever computes a real volume -- this stream must never overwrite it
    with its own guess, since save_market_price() replaces the whole
    stored MarketPrice row, not just the price field, and MarketPrice
    requires a volume (no way to just not send one)."""
    seeded = MarketPrice(symbol="SPY", as_of=NOW, price=Decimal("551.00"), volume=16_396_508)
    storage = _FakeStorage(seed=seeded)
    provider = _FakeProvider(trades=[_trade("552.25")])
    use_case = StreamUnderlyingPriceUseCase(provider, storage)

    await use_case.run("SPY")

    assert len(storage.saved) == 1
    saved = storage.saved[0]
    assert saved.price == Decimal("552.25")  # price still updates
    assert saved.volume == 16_396_508  # volume from the scheduler survives intact


@pytest.mark.asyncio
async def test_volume_survives_several_stream_ticks_in_a_row() -> None:
    """Not just one tick -- the scheduler's volume must keep surviving
    across an unbroken run of stream writes between scheduler cycles,
    since each tick's write is itself what the next tick reads back."""
    seeded = MarketPrice(symbol="SPY", as_of=NOW, price=Decimal("551.00"), volume=16_396_508)
    storage = _FakeStorage(seed=seeded)
    provider = _FakeProvider(trades=[_trade("552.25"), _trade("552.30"), _trade("552.35")])
    # A window of 0 so every one of the 3 fixture trades is persisted,
    # not debounced away -- isolates this test to the carry-forward
    # behavior itself.
    use_case = StreamUnderlyingPriceUseCase(provider, storage, min_write_interval_seconds=0.0)

    await use_case.run("SPY")

    assert [p.price for p in storage.saved] == [Decimal("552.25"), Decimal("552.30"), Decimal("552.35")]
    assert all(p.volume == 16_396_508 for p in storage.saved)


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


class TestMarketHoursGate:
    """The real Trade Stream keeps printing outside 09:30-16:00 ET
    (extended hours) -- confirmed live, 2026-09: a tick as late as 17:11
    ET reached storage before this gate existed, and dashboard.tsx has
    no filter of its own on what it polls from MarketPrice.as_of, so an
    extended-hours tick that reached storage always reached the chart.
    NOW (2026-01-15T14:30:00Z) is itself exactly 09:30 ET -- the open
    boundary -- confirmed included, so every other test in this file
    using the default `as_of` is unaffected by this gate."""

    @pytest.mark.asyncio
    async def test_a_tick_after_the_close_is_not_persisted(self) -> None:
        storage = _FakeStorage()
        # 17:11 ET on the same Thursday -- the real distance confirmed
        # live past the 16:00 ET close.
        after_close = datetime(2026, 1, 15, 22, 11, tzinfo=UTC)
        provider = _FakeProvider(trades=[_trade("552.25", as_of=after_close)])

        await StreamUnderlyingPriceUseCase(provider, storage).run("SPY")

        assert storage.saved == []

    @pytest.mark.asyncio
    async def test_a_tick_before_the_open_is_not_persisted(self) -> None:
        storage = _FakeStorage()
        # 09:00 ET the same Thursday, before the 09:30 open.
        before_open = datetime(2026, 1, 15, 14, 0, tzinfo=UTC)
        provider = _FakeProvider(trades=[_trade("552.25", as_of=before_open)])

        await StreamUnderlyingPriceUseCase(provider, storage).run("SPY")

        assert storage.saved == []

    @pytest.mark.asyncio
    async def test_a_tick_exactly_at_the_close_boundary_is_not_persisted(self) -> None:
        storage = _FakeStorage()
        # 16:00:00 ET exactly -- is_market_open's interval is half-open
        # ([9:30, 16:00)), so this must be excluded, not the last tick in.
        at_close = datetime(2026, 1, 15, 21, 0, tzinfo=UTC)
        provider = _FakeProvider(trades=[_trade("552.25", as_of=at_close)])

        await StreamUnderlyingPriceUseCase(provider, storage).run("SPY")

        assert storage.saved == []

    @pytest.mark.asyncio
    async def test_a_weekend_tick_is_not_persisted_regardless_of_time_of_day(self) -> None:
        storage = _FakeStorage()
        # Saturday 2026-01-17, 10:00 ET -- inside the daily clock window
        # but on a non-trading day.
        saturday = datetime(2026, 1, 17, 15, 0, tzinfo=UTC)
        provider = _FakeProvider(trades=[_trade("552.25", as_of=saturday)])

        await StreamUnderlyingPriceUseCase(provider, storage).run("SPY")

        assert storage.saved == []

    @pytest.mark.asyncio
    async def test_ticks_during_market_hours_still_persist_around_excluded_ones(self) -> None:
        """Scope check -- the gate drops only the out-of-hours ticks, not
        the whole stream once one of them shows up."""
        storage = _FakeStorage()
        before_open = datetime(2026, 1, 15, 14, 0, tzinfo=UTC)
        during_hours = datetime(2026, 1, 15, 14, 30, tzinfo=UTC)
        after_close = datetime(2026, 1, 15, 22, 11, tzinfo=UTC)
        provider = _FakeProvider(
            trades=[
                _trade("551.00", as_of=before_open),
                _trade("552.25", as_of=during_hours),
                _trade("553.50", as_of=after_close),
            ],
        )
        use_case = StreamUnderlyingPriceUseCase(provider, storage, min_write_interval_seconds=0.0)

        await use_case.run("SPY")

        assert [p.price for p in storage.saved] == [Decimal("552.25")]
