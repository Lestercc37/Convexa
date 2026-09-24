from __future__ import annotations

import asyncio
from datetime import date

import pytest

from backend.adapters.providers.thetadata.provider import (
    CONTRACT_REDISCOVERY_INTERVAL_SECONDS,
    ThetaDataProvider,
    _NearTheMoneyChain,
)
from backend.domain.underlyings import ACTIVE_UNDERLYINGS_BY_SYMBOL

REST_URL = "http://thetaterminal.test"
WS_URL = "ws://thetaterminal.test/v1/events"

# Every active symbol, futures included -- start()'s own original
# options-discovery loop (the one this periodic version mirrors) has
# never filtered futures out here, only from the separate underlying-
# price registration loop just above it (futures options still exist
# and still need discovering, even though a future's own spot price
# isn't streamed the same way a stock/index's is).
ALL_SYMBOLS = list(ACTIVE_UNDERLYINGS_BY_SYMBOL)


def _provider() -> ThetaDataProvider:
    return ThetaDataProvider(REST_URL, WS_URL)


class TestPeriodicContractRediscovery:
    """Added alongside the scheduler/stream process split (2026-09-22):
    get_option_chain()'s own _register_streaming_contracts() call (the
    scheduler's REST cycle) used to be what caught a contract newly
    near-the-money since startup -- see TestNearTheMoneyResubscription's
    own docstring. With the scheduler moved to its own process
    (backend/scheduler_worker.py), that process's provider instance
    never has its hub started, so its own get_option_chain() calls no
    longer do anything useful for a hub that's never running. This
    process's own start() now runs the same discovery on its own timer
    instead."""

    def test_calls_fetch_and_register_for_every_active_symbol(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        provider = _provider()
        fetch_calls: list[str] = []
        register_calls: list[tuple[str, _NearTheMoneyChain]] = []
        fake_chain = _NearTheMoneyChain(date(2099, 1, 1), [])

        def fake_fetch(symbol: str, expiration: date | None) -> _NearTheMoneyChain:
            fetch_calls.append(symbol)
            return fake_chain

        def fake_register(symbol: str, chain: _NearTheMoneyChain) -> None:
            register_calls.append((symbol, chain))

        monkeypatch.setattr(provider, "_fetch_near_the_money", fake_fetch)
        monkeypatch.setattr(provider, "_register_streaming_contracts", fake_register)

        sleep_calls: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)
            if len(sleep_calls) >= 2:
                raise asyncio.CancelledError

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)

        async def run() -> None:
            with pytest.raises(asyncio.CancelledError):
                await provider._periodic_contract_rediscovery()

        asyncio.run(run())

        assert sleep_calls == [CONTRACT_REDISCOVERY_INTERVAL_SECONDS] * 2
        assert sorted(fetch_calls) == sorted(ALL_SYMBOLS)
        assert sorted(symbol for symbol, _ in register_calls) == sorted(ALL_SYMBOLS)

    def test_one_symbols_fetch_failure_does_not_stop_the_others(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        provider = _provider()
        fetch_calls: list[str] = []
        register_calls: list[str] = []
        fake_chain = _NearTheMoneyChain(date(2099, 1, 1), [])

        def fake_fetch(symbol: str, expiration: date | None) -> _NearTheMoneyChain:
            fetch_calls.append(symbol)
            if symbol == "SPY":
                # Matches _periodic_contract_rediscovery's own except
                # clause -- the same (httpx.HTTPError, ValueError) pair
                # start()'s own one-time discovery loop already catches,
                # a real ThetaData REST failure shape, not an arbitrary
                # exception this method was never meant to survive.
                raise ValueError("simulated ThetaData failure for SPY")
            return fake_chain

        def fake_register(symbol: str, chain: _NearTheMoneyChain) -> None:
            register_calls.append(symbol)

        monkeypatch.setattr(provider, "_fetch_near_the_money", fake_fetch)
        monkeypatch.setattr(provider, "_register_streaming_contracts", fake_register)

        sleep_calls: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)
            if len(sleep_calls) >= 2:
                raise asyncio.CancelledError

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)

        async def run() -> None:
            with pytest.raises(asyncio.CancelledError):
                await provider._periodic_contract_rediscovery()

        asyncio.run(run())

        # SPY was attempted (and failed) but every other symbol still
        # got fetched and registered -- one symbol's ThetaData error
        # must never take the whole rediscovery pass down with it.
        assert "SPY" in fetch_calls
        assert "SPY" not in register_calls
        assert set(register_calls) == set(ALL_SYMBOLS) - {"SPY"}

    def test_offloads_the_blocking_fetch_to_a_thread_instead_of_calling_it_directly(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression (2026-09-24, live market-open incident): _fetch_near_
        the_money is a plain sync method backed by a blocking httpx.Client
        (not AsyncClient) -- calling it directly from this coroutine blocks
        the same event loop ThetaStreamHub's own WS read loop runs on.
        Confirmed live: with REST latency degraded under real market-open
        load ("ThetaData request slow... took 6.75s" observed), that
        blocking held the loop long enough to starve websocket.recv() past
        every staleness watchdog's own threshold, forcing a reconnect
        cycle roughly every 90-120s through the first 10+ minutes of the
        session -- and each reconnect resubscribing the full contract set
        (1720 contracts / 3440 messages observed) only made the next pass
        more likely to overlap another slow stretch. asyncio.to_thread is
        what must carry this call now, not a direct invocation."""
        provider = _provider()
        fake_chain = _NearTheMoneyChain(date(2099, 1, 1), [])
        to_thread_calls: list[tuple[object, tuple[object, ...], dict[str, object]]] = []

        async def fake_to_thread(func, /, *args: object, **kwargs: object):
            to_thread_calls.append((func, args, kwargs))
            return fake_chain

        monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)
        monkeypatch.setattr(provider, "_register_streaming_contracts", lambda *a: None)

        sleep_calls: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)
            if len(sleep_calls) >= 2:
                raise asyncio.CancelledError

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)

        async def run() -> None:
            with pytest.raises(asyncio.CancelledError):
                await provider._periodic_contract_rediscovery()

        asyncio.run(run())

        assert len(to_thread_calls) == len(ALL_SYMBOLS)
        for func, args, kwargs in to_thread_calls:
            assert func == provider._fetch_near_the_money
            assert args[0] in ALL_SYMBOLS
            assert kwargs == {"expiration": None}
