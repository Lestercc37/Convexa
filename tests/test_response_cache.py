"""The shared few-second cache of /market (backend/api/response_cache.py)."""

from __future__ import annotations

import asyncio

import pytest

from backend.api.response_cache import TtlSingleFlight


def test_a_value_is_reused_until_it_expires_and_rebuilt_after() -> None:
    now = [0.0]
    cache: TtlSingleFlight[int] = TtlSingleFlight(3.0, clock=lambda: now[0])
    builds = []

    async def build() -> int:
        builds.append(1)
        return len(builds)

    async def scenario() -> list[int]:
        first = await cache.get("SPX", build)
        now[0] = 2.9
        second = await cache.get("SPX", build)
        now[0] = 3.1
        third = await cache.get("SPX", build)
        other = await cache.get("NDX", build)
        return [first, second, third, other]

    assert asyncio.run(scenario()) == [1, 1, 2, 3]


def test_concurrent_requests_share_one_build() -> None:
    cache: TtlSingleFlight[str] = TtlSingleFlight(3.0)
    builds = []

    async def build() -> str:
        builds.append(1)
        await asyncio.sleep(0.01)
        return "snapshot"

    async def scenario() -> list[str]:
        return await asyncio.gather(*(cache.get("SPY", build) for _ in range(5)))

    assert asyncio.run(scenario()) == ["snapshot"] * 5
    assert len(builds) == 1


def test_errors_are_not_cached() -> None:
    cache: TtlSingleFlight[str] = TtlSingleFlight(3.0)
    calls = []

    async def failing() -> str:
        calls.append(1)
        raise RuntimeError("db down")

    async def scenario() -> None:
        with pytest.raises(RuntimeError):
            await cache.get("QQQ", failing)
        with pytest.raises(RuntimeError):
            await cache.get("QQQ", failing)

    asyncio.run(scenario())
    assert len(calls) == 2
