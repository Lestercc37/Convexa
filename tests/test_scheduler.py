from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from backend.core.container import build_container
from backend.core.scheduler import UnderlyingRefreshScheduler
from backend.domain.underlyings import ACTIVE_UNDERLYINGS

ACTIVE_SYMBOLS = [underlying.symbol for underlying in ACTIVE_UNDERLYINGS]


class _StubRefreshUseCase:
    def __init__(self, fail_for: frozenset[str] = frozenset()) -> None:
        self.calls: list[str] = []
        self._fail_for = fail_for

    def execute(self, symbol: str) -> tuple[None, None]:
        self.calls.append(symbol)
        if symbol in self._fail_for:
            raise RuntimeError(f"provider failed for {symbol}")
        return (None, None)


def _scheduler_with_stub(
    interval_seconds: float = 30.0, fail_for: frozenset[str] = frozenset()
) -> tuple[UnderlyingRefreshScheduler, _StubRefreshUseCase]:
    stub = _StubRefreshUseCase(fail_for=fail_for)
    container = replace(build_container(), refresh_underlying_snapshot_use_case=stub)
    return UnderlyingRefreshScheduler(container, interval_seconds=interval_seconds), stub


@pytest.mark.asyncio
async def test_cycle_processes_all_11_active_symbols() -> None:
    scheduler, stub = _scheduler_with_stub()

    await scheduler._run_cycle()

    assert len(ACTIVE_SYMBOLS) == 11
    assert stub.calls == ACTIVE_SYMBOLS


@pytest.mark.asyncio
async def test_cycle_continues_for_remaining_symbols_when_one_fails() -> None:
    scheduler, stub = _scheduler_with_stub(fail_for=frozenset({"QQQ"}))

    await scheduler._run_cycle()

    # Every symbol was still attempted — one failure didn't stop the cycle.
    assert stub.calls == ACTIVE_SYMBOLS


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
