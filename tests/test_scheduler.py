from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import replace
from datetime import date, timedelta

import httpx
import pytest

from backend.adapters.providers.mock.fake import FakeGreeksCalculator
from backend.adapters.providers.mock.gamma_aggregate import FakeGammaAggregateCalculator
from backend.adapters.providers.mock.gamma_exposure import FakeGammaExposureCalculator
from backend.adapters.providers.mock.gamma_flip import FakeGammaFlipCalculator
from backend.adapters.providers.mock.max_pain import FakeMaxPainCalculator
from backend.adapters.providers.mock.walls import FakeWallCalculator
from backend.adapters.providers.thetadata.provider import (
    THETADATA_MAX_CONCURRENT_REQUESTS,
    ThetaDataProvider,
)
from backend.adapters.storage.memory import InMemoryStorage
from backend.core.container import build_container
from backend.core.scheduler import UnderlyingRefreshScheduler
from backend.domain.underlyings import ACTIVE_UNDERLYINGS
from backend.domain.use_cases import (
    CalculateDerivedMetricsUseCase,
    CalculateGammaAggregateUseCase,
    CalculateGammaExposureOrchestrator,
    CalculateGammaFlipUseCase,
    CalculateGreeksUseCase,
    CalculateMaxPainUseCase,
    CalculateWallsUseCase,
    RefreshUnderlyingSnapshotUseCase,
    WhaleAlertsEngine,
)

ACTIVE_SYMBOLS = [underlying.symbol for underlying in ACTIVE_UNDERLYINGS]


class _StubRefreshUseCase:
    def __init__(self, fail_for: frozenset[str] = frozenset(), delay_seconds: float = 0.0) -> None:
        self.calls: list[str] = []
        self._fail_for = fail_for
        self._delay_seconds = delay_seconds

    def execute(self, symbol: str) -> tuple[None, None]:
        if self._delay_seconds:
            time.sleep(self._delay_seconds)
        self.calls.append(symbol)
        if symbol in self._fail_for:
            raise RuntimeError(f"provider failed for {symbol}")
        return (None, None)


def _scheduler_with_stub(
    interval_seconds: float = 30.0,
    fail_for: frozenset[str] = frozenset(),
    delay_seconds: float = 0.0,
) -> tuple[UnderlyingRefreshScheduler, _StubRefreshUseCase]:
    stub = _StubRefreshUseCase(fail_for=fail_for, delay_seconds=delay_seconds)
    container = replace(build_container(), refresh_underlying_snapshot_use_case=stub)
    return UnderlyingRefreshScheduler(container, interval_seconds=interval_seconds), stub


@pytest.mark.asyncio
async def test_cycle_processes_all_11_active_symbols() -> None:
    scheduler, stub = _scheduler_with_stub()

    await scheduler._run_cycle()

    assert len(ACTIVE_SYMBOLS) == 11
    # Order is no longer guaranteed — symbols are dispatched concurrently
    # (asyncio.gather), not one after another — so this checks the same
    # set of symbols was processed, not that they arrived in list order.
    assert sorted(stub.calls) == sorted(ACTIVE_SYMBOLS)


@pytest.mark.asyncio
async def test_cycle_continues_for_remaining_symbols_when_one_fails() -> None:
    scheduler, stub = _scheduler_with_stub(fail_for=frozenset({"QQQ"}))

    await scheduler._run_cycle()

    # Every symbol was still attempted — one failure didn't stop the
    # cycle (order not guaranteed under concurrent dispatch, see above).
    assert sorted(stub.calls) == sorted(ACTIVE_SYMBOLS)


@pytest.mark.asyncio
async def test_loop_does_not_run_a_cycle_outside_market_hours(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("backend.core.scheduler.is_market_open", lambda now: False)
    scheduler, stub = _scheduler_with_stub(interval_seconds=0.01)

    scheduler.start()
    await asyncio.sleep(0.05)
    await scheduler.stop()

    assert stub.calls == []


@pytest.mark.asyncio
async def test_loop_runs_a_cycle_during_market_hours(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("backend.core.scheduler.is_market_open", lambda now: True)
    scheduler, stub = _scheduler_with_stub(interval_seconds=0.01)

    scheduler.start()
    await asyncio.sleep(0.05)
    await scheduler.stop()

    assert ACTIVE_SYMBOLS[0] in stub.calls


@pytest.mark.asyncio
async def test_start_is_idempotent_and_stop_before_start_is_a_no_op() -> None:
    scheduler, _ = _scheduler_with_stub()

    await scheduler.stop()  # never started — must not raise

    scheduler.start()
    first_task = scheduler._task
    scheduler.start()  # second call must not replace the running task
    assert scheduler._task is first_task

    await scheduler.stop()
    assert scheduler._task is None


@pytest.mark.asyncio
async def test_run_cycle_dispatches_symbols_concurrently_not_sequentially() -> None:
    # Measured live against real ATR-widened chain widths (2026-09
    # investigation): a fully sequential cycle across these same 11
    # symbols took ~30s — nearly the scheduler's own 30s interval, since
    # `_run` sleeps *after* a cycle finishes, pushing the effective
    # refresh cadence closer to ~60s. 11 symbols x a 0.15s per-symbol
    # delay would take ~1.65s sequential; concurrent dispatch should
    # finish close to a single delay, not eleven of them.
    scheduler, stub = _scheduler_with_stub(delay_seconds=0.15)

    start = time.monotonic()
    await scheduler._run_cycle()
    elapsed = time.monotonic() - start

    assert sorted(stub.calls) == sorted(ACTIVE_SYMBOLS)
    # Comfortably below 11 x 0.15s = 1.65s (sequential) and above a
    # single 0.15s call, leaving real margin for thread-dispatch overhead.
    assert elapsed < 0.6


def _minimal_first_order_entry(symbol: str) -> dict[str, object]:
    return {
        "contract": {"symbol": symbol, "expiration": "2026-09-18", "right": "CALL", "strike": 100.0},
        "data": [
            {
                "underlying_price": 100.0,
                "delta": 0.5,
                "implied_vol": 0.2,
                "theta": -1.0,
                "vega": 1.0,
                "bid": 1.0,
                "ask": 1.1,
                "timestamp": "2026-08-31T14:35:22.752",
            }
        ],
    }


def _minimal_daily_bars_response() -> dict[str, object]:
    rows = []
    for offset in range(20, 0, -1):
        day = date(2026, 8, 31) - timedelta(days=offset)
        rows.append(
            {
                "last_trade": f"{day.isoformat()}T16:00:00.000",
                "open": 99.5,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
            }
        )
    return {"response": rows}


def _real_refresh_use_case_with_transport(handler) -> RefreshUnderlyingSnapshotUseCase:
    """A RefreshUnderlyingSnapshotUseCase wired to a real ThetaDataProvider
    (mocked HTTP transport, real threading.Semaphore) and real calculators
    — everything the scheduler actually drives per symbol, so a test using
    this exercises the genuine REST concurrency chokepoint, not a stub
    that bypasses it."""
    provider = ThetaDataProvider("http://thetaterminal.test", "ws://thetaterminal.test/v1/events")
    provider._client = httpx.Client(
        base_url="http://thetaterminal.test", transport=httpx.MockTransport(handler)
    )
    storage = InMemoryStorage()
    orchestrator = CalculateGammaExposureOrchestrator(
        storage=storage,
        greeks=CalculateGreeksUseCase(FakeGreeksCalculator()),
        aggregate=CalculateGammaAggregateUseCase(
            FakeGammaExposureCalculator(), FakeGammaAggregateCalculator()
        ),
        gamma_flip=CalculateGammaFlipUseCase(FakeGammaFlipCalculator()),
        walls=CalculateWallsUseCase(FakeWallCalculator()),
        max_pain=CalculateMaxPainUseCase(FakeMaxPainCalculator()),
    )
    return RefreshUnderlyingSnapshotUseCase(
        storage=storage,
        market_data_provider=provider,
        whale_alerts_engine=WhaleAlertsEngine(storage=storage),
        gamma_exposure_orchestrator=orchestrator,
        derived_metrics_use_case=CalculateDerivedMetricsUseCase(storage),
    )


@pytest.mark.asyncio
async def test_semaphore_still_caps_real_rest_concurrency_under_parallel_dispatch() -> None:
    # The scheduler now fires all 11 symbols at once — this proves the
    # real ThetaDataProvider semaphore (PR #86), not the scheduler, is
    # what keeps concurrent REST calls at the documented account cap,
    # exactly as intended: the scheduler no longer needs to know about
    # that limit at all.
    in_flight = 0
    max_observed = 0
    lock = threading.Lock()
    release_event = threading.Event()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal in_flight, max_observed
        url = str(request.url)
        if "greeks/first_order" in url:
            symbol = request.url.params.get("symbol", "SPY")
            with lock:
                in_flight += 1
                max_observed = max(max_observed, in_flight)
            release_event.wait(timeout=5)
            with lock:
                in_flight -= 1
            return httpx.Response(200, json={"response": [_minimal_first_order_entry(symbol)]})
        if "open_interest" in url:
            return httpx.Response(200, json={"response": []})
        if "interest_rate/history/eod" in url:
            return httpx.Response(200, json={"response": [{"rate": 3.64, "created": "2026-08-31"}]})
        if "history/eod" in url:  # stock or index daily bars
            return httpx.Response(200, json=_minimal_daily_bars_response())
        raise AssertionError(f"unexpected request: {request.url}")

    use_case = _real_refresh_use_case_with_transport(handler)
    container = replace(build_container(), refresh_underlying_snapshot_use_case=use_case)
    scheduler = UnderlyingRefreshScheduler(container)

    cycle_task = asyncio.create_task(scheduler._run_cycle())
    try:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and in_flight < THETADATA_MAX_CONCURRENT_REQUESTS:
            await asyncio.sleep(0.01)
        # Give the remaining queued symbols a moment to prove they stay
        # blocked on the semaphore rather than sneaking past the cap.
        await asyncio.sleep(0.2)

        assert in_flight == THETADATA_MAX_CONCURRENT_REQUESTS
        assert max_observed == THETADATA_MAX_CONCURRENT_REQUESTS
    finally:
        release_event.set()
        await asyncio.wait_for(cycle_task, timeout=5)

    assert in_flight == 0
