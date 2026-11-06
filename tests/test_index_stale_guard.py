from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from backend.adapters.providers.thetadata.stream_parsing import ParsedUnderlyingTrade
from backend.domain.entities import UnderlyingTradeEvent
from backend.domain.use_cases.index_stale_guard import (
    DRIFT_HOLD,
    LAG_LIMIT,
    MIN_SAMPLES,
    RECOVER_HOLD,
    SAMPLE_EVERY,
    SILENCE_LIMIT,
    IndexPriceFallback,
    IndexStaleGuard,
)
from backend.stream_processor_worker import _guard_allows, _ProcessorState

OPEN = datetime(2026, 11, 6, 14, 30, tzinfo=UTC)
RATIO = 10.03


def _healthy(guard: IndexStaleGuard, seconds: int, start: datetime = OPEN, spy: float = 740.0) -> datetime:
    """One SPY trade and one SPX tick (exactly RATIO x SPY, stamped on time) per second."""
    at = start
    for second in range(seconds):
        at = start + timedelta(seconds=second)
        assert guard.observe_proxy("SPY", Decimal(str(spy)), at) == []
        assert guard.observe_index("SPX", Decimal(str(round(spy * RATIO, 2))), at, at) is True
    return at


def test_healthy_ticks_are_written_and_never_replaced() -> None:
    guard = IndexStaleGuard()
    _healthy(guard, 600)
    assert not guard.active("SPX")
    assert guard.drain_changes() == []


def test_a_lagging_index_is_replaced_by_spy_times_the_good_ratio_and_comes_back_after_the_recovery_hold() -> None:
    guard = IndexStaleGuard()
    at = _healthy(guard, MIN_SAMPLES * int(SAMPLE_EVERY.total_seconds()) + 5)
    late = at + timedelta(seconds=1)
    # Stamped 9 minutes ago (ThetaData 2026-11-06): dropped, and SPY now writes SPX.
    assert guard.observe_index("SPX", Decimal("7400.97"), late - timedelta(minutes=9), late) is False
    assert guard.active("SPX")
    [change] = guard.drain_changes()
    assert (change.symbol, change.proxy, change.active, change.reason) == ("SPX", "SPY", True, "lag")
    assert change.ratio == pytest.approx(RATIO)
    assert guard.observe_proxy("SPY", Decimal("742.50"), late) == [("SPX", Decimal(str(round(742.50 * RATIO, 2))))]
    # NDX is not touched by SPX's episode.
    assert not guard.active("NDX")

    # Fresh, aligned ticks again: still replaced until RECOVER_HOLD has passed, then written again.
    back = late + timedelta(seconds=1)
    held = int(RECOVER_HOLD.total_seconds())
    for second in range(held + 1):
        now = back + timedelta(seconds=second)
        guard.observe_proxy("SPY", Decimal("742.50"), now)
        allowed = guard.observe_index("SPX", Decimal(str(round(742.50 * RATIO, 2))), now, now)
        assert allowed is (second >= held)
    assert not guard.active("SPX")
    [end] = guard.drain_changes()
    assert (end.active, end.reason) == (False, "recovered")


def test_a_drifting_ratio_only_counts_once_held_for_the_drift_hold() -> None:
    guard = IndexStaleGuard()
    at = _healthy(guard, 120)
    hold = int(DRIFT_HOLD.total_seconds())
    # SPX 0.2 % under its usual ratio, stamped on time (stale values on fresh timestamps): needs the recent median to move AND the hold.
    activated_after = None
    for second in range(1, 4 * hold):
        now = at + timedelta(seconds=second)
        guard.observe_proxy("SPY", Decimal("740.0"), now)
        if guard.observe_index("SPX", Decimal(str(round(740.0 * RATIO * 0.998, 2))), now, now) is False:
            activated_after = second
            break
    assert activated_after is not None and activated_after >= hold
    assert guard.drain_changes()[0].reason == "drift"


def test_a_silent_index_is_replaced_once_the_proxy_keeps_trading_past_the_silence_limit() -> None:
    guard = IndexStaleGuard()
    at = _healthy(guard, 120)
    quiet = int(SILENCE_LIMIT.total_seconds())
    assert guard.observe_proxy("SPY", Decimal("740.0"), at + timedelta(seconds=quiet - 1)) == []
    assert guard.observe_proxy("SPY", Decimal("741.0"), at + timedelta(seconds=quiet + 1)) == [("SPX", Decimal(str(round(741.0 * RATIO, 2))))]
    assert guard.drain_changes()[0].reason == "silent"


def test_nothing_is_invented_without_a_good_ratio() -> None:
    guard = IndexStaleGuard()
    guard.observe_proxy("SPY", Decimal("740.0"), OPEN)
    # A late tick on the first second of the day: no healthy samples yet, so the tick is written as it is.
    assert guard.observe_index("SPX", Decimal("7400"), OPEN - LAG_LIMIT * 10, OPEN) is True
    assert guard.observe_proxy("SPY", Decimal("740.0"), OPEN + SILENCE_LIMIT * 2) == []
    assert not guard.active("SPX")


def test_the_heartbeat_status_keeps_the_reason_and_the_start() -> None:
    guard = IndexStaleGuard()
    at = _healthy(guard, 120)
    guard.observe_index("SPX", Decimal("7400"), at - timedelta(minutes=5), at)
    status = guard.status("SPX", at, at + timedelta(seconds=30))
    assert status == IndexPriceFallback("SPX", "SPY", True, pytest.approx(RATIO), "lag", at, at + timedelta(seconds=30))
    assert guard.status("NDX", at, at) is None


class _Relay:
    def publish_quote(self, event) -> None: ...
    def publish_trade(self, event) -> None: ...


class _Prices:
    def __init__(self) -> None:
        self.calls: list[UnderlyingTradeEvent] = []

    async def persist_if_due(self, event: UnderlyingTradeEvent) -> None:
        self.calls.append(event)


@pytest.mark.asyncio
async def test_the_stream_processor_drops_a_lagging_spx_tick_and_writes_spx_from_spy() -> None:
    prices = _Prices()
    state = _ProcessorState(_Relay(), prices)  # type: ignore[arg-type]
    at = _healthy(state.index_guard, 120)

    def trade(symbol: str, price: str, now: datetime, stamped: datetime | None) -> ParsedUnderlyingTrade:
        return ParsedUnderlyingTrade(UnderlyingTradeEvent(symbol=symbol, as_of=now, price=Decimal(price), size=1, condition=0), stamped)

    now = at + timedelta(seconds=1)
    assert _guard_allows(state, trade("SPX", "7400.97", now, now - timedelta(minutes=9))) is False
    assert _guard_allows(state, trade("SPY", "742.50", now, now)) is True
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert [(call.symbol, call.price) for call in prices.calls] == [("SPX", Decimal(str(round(742.50 * RATIO, 2))))]
    # A symbol the guard does not watch is never touched.
    assert _guard_allows(state, trade("IWM", "271.70", now, now)) is True


def test_price_sources_lists_only_active_and_fresh_replacements() -> None:
    from backend.main import app

    now = datetime.now(UTC)
    with TestClient(app) as client:
        storage = client.app.state.container.storage
        storage.save_index_price_fallback(IndexPriceFallback("SPX", "SPY", True, 10.0296, "drift", now - timedelta(minutes=5), now))
        storage.save_index_price_fallback(IndexPriceFallback("NDX", "QQQ", True, 41.11, "lag", now - timedelta(hours=1), now - timedelta(minutes=10)))
        response = client.get("/api/v1/price-sources")
    assert response.status_code == 200
    body = response.json()
    assert [item["symbol"] for item in body["replaced"]] == ["SPX"]
    assert body["replaced"][0]["proxy"] == "SPY" and body["replaced"][0]["reason"] == "drift"
