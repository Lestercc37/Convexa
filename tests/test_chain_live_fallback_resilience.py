"""GET /chain/{symbol}'s live fallback must not turn a provider failure into a 500 when a stored chain exists.

2026-10-15 10:31: "ThetaData returned no near-the-money contracts for IWM" (the Terminal was dropping
packets) raised out of the live fallback although the scheduler's snapshot was only minutes old."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

import pytest

from backend.adapters.storage.memory import InMemoryStorage
from backend.adapters.storage.sync_read_adapter import SyncStorageAsyncReadAdapter
from backend.domain.use_cases.read_models import (
    CHAIN_STORED_MAX_AGE_SECONDS,
    get_option_chain,
    get_option_chain_async,
)
from tests.test_chain_fallback_does_not_write import EXPIRATION, _chain, market_open  # noqa: F401


class _FailingProvider:
    def __init__(self, error: Exception) -> None:
        self.calls = 0
        self._error = error

    def get_option_chain(self, underlying: str, expiration: date | None = None):
        self.calls += 1
        raise self._error


def _stale_storage() -> tuple[InMemoryStorage, datetime]:
    storage = InMemoryStorage()
    stale_as_of = datetime.now(UTC) - timedelta(seconds=CHAIN_STORED_MAX_AGE_SECONDS + 240)
    storage.save_chain_snapshot(_chain(stale_as_of, volume=111))
    return storage, stale_as_of


def test_a_failed_live_fetch_serves_the_stored_chain(market_open, caplog) -> None:  # noqa: F811
    storage, stale_as_of = _stale_storage()
    provider = _FailingProvider(RuntimeError("ThetaData returned no near-the-money contracts for SPX"))

    with caplog.at_level(logging.WARNING, logger="backend.domain.use_cases.read_models"):
        chain = get_option_chain(storage, provider, "SPX", EXPIRATION)

    assert provider.calls == 1
    assert chain.as_of == stale_as_of
    assert "serving the stored snapshot" in caplog.text


def test_without_any_stored_chain_the_failure_still_surfaces(market_open) -> None:  # noqa: F811
    provider = _FailingProvider(RuntimeError("ThetaData returned no near-the-money contracts for SPX"))

    with pytest.raises(RuntimeError, match="no near-the-money"):
        get_option_chain(InMemoryStorage(), provider, "SPX", EXPIRATION)


def test_only_provider_failures_are_absorbed(market_open) -> None:  # noqa: F811
    storage, _ = _stale_storage()
    provider = _FailingProvider(ValueError("a programming error is not a provider outage"))

    with pytest.raises(ValueError, match="programming error"):
        get_option_chain(storage, provider, "SPX", EXPIRATION)


@pytest.mark.asyncio
async def test_the_async_route_path_gets_the_same_protection(market_open) -> None:  # noqa: F811
    storage, stale_as_of = _stale_storage()
    provider = _FailingProvider(RuntimeError("ThetaData request failed: 500"))

    chain = await get_option_chain_async(
        SyncStorageAsyncReadAdapter(storage), storage, provider, "SPX", EXPIRATION
    )

    assert chain.as_of == stale_as_of
