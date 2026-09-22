"""get_option_chain's 60s freshness TTL must respect is_market_open --
outside market hours it should serve the stored snapshot no matter its
age instead of refreshing live, matching the gamma read path
(get_gamma_exposure), which never calls the provider at all. Before
this fix, this was the one path that still hit the live provider after
close every time a user reopened the dashboard past the 60s TTL --
confirmed live, 2026-09 investigation: it overwrote a good pre-close
snapshot with a degenerate all-zero-gamma one, once today's
already-expired 0DTE lost its time value in calculate_bsm_greeks."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from backend.adapters.storage.memory import InMemoryStorage
from backend.adapters.storage.sync_read_adapter import SyncStorageAsyncReadAdapter
from backend.domain.entities import ContractType, Greeks, OptionChain, OptionContract
from backend.domain.use_cases.read_models import get_option_chain, get_option_chain_async


def _chain(as_of: datetime) -> OptionChain:
    return OptionChain(
        symbol="SPY",
        as_of=as_of,
        spot_price=Decimal(550),
        contracts=(
            OptionContract(
                underlying="SPY",
                strike=Decimal(550),
                expiration=date(2026, 2, 20),
                contract_type=ContractType.CALL,
                occ_symbol="SPY260220C00550000",
                bid=Decimal(1),
                ask=Decimal("1.10"),
                last=Decimal("1.05"),
                volume=100,
                open_interest=100,
                iv=Decimal("0.20"),
                greeks=Greeks(
                    delta=Decimal("0.50"),
                    gamma=Decimal("0.05"),
                    theta=Decimal("-0.10"),
                    vega=Decimal("0.20"),
                    charm=Decimal("0.01"),
                    vanna=Decimal("0.02"),
                ),
            ),
        ),
    )


class _FakeProvider:
    """Records call count -- the point of every test here is whether the
    live provider gets hit at all, not what it returns."""

    def __init__(self, chain: OptionChain) -> None:
        self._chain = chain
        self.calls = 0

    def get_option_chain(self, underlying: str, expiration: date | None = None) -> OptionChain:
        self.calls += 1
        return self._chain


def test_serves_the_stale_snapshot_without_a_live_call_outside_market_hours(monkeypatch) -> None:
    storage = InMemoryStorage()
    stale_as_of = datetime.now(UTC) - timedelta(hours=2)
    storage.save_chain_snapshot(_chain(stale_as_of))
    provider = _FakeProvider(_chain(datetime.now(UTC)))
    monkeypatch.setattr("backend.domain.use_cases.read_models.is_market_open", lambda now: False)

    chain = get_option_chain(storage, provider, "SPY")

    assert chain.as_of == stale_as_of
    assert provider.calls == 0


def test_refreshes_live_when_stale_during_market_hours(monkeypatch) -> None:
    """Scope check -- the fix is only about outside-hours behavior; the
    existing during-hours refresh-on-stale behavior must be untouched."""
    storage = InMemoryStorage()
    stale_as_of = datetime.now(UTC) - timedelta(hours=2)
    storage.save_chain_snapshot(_chain(stale_as_of))
    fresh_chain = _chain(datetime.now(UTC))
    provider = _FakeProvider(fresh_chain)
    monkeypatch.setattr("backend.domain.use_cases.read_models.is_market_open", lambda now: True)

    chain = get_option_chain(storage, provider, "SPY")

    assert chain.as_of == fresh_chain.as_of
    assert provider.calls == 1


def test_fresh_snapshot_is_served_without_a_live_call_regardless_of_market_hours(monkeypatch) -> None:
    """Scope check -- inside the 60s TTL, behavior is unchanged whether
    the market is open or closed: no live call either way."""
    storage = InMemoryStorage()
    fresh_as_of = datetime.now(UTC) - timedelta(seconds=5)
    storage.save_chain_snapshot(_chain(fresh_as_of))
    provider = _FakeProvider(_chain(datetime.now(UTC)))
    monkeypatch.setattr("backend.domain.use_cases.read_models.is_market_open", lambda now: False)

    chain = get_option_chain(storage, provider, "SPY")

    assert chain.as_of == fresh_as_of
    assert provider.calls == 0


def test_calls_the_provider_when_no_snapshot_exists_even_outside_market_hours(monkeypatch) -> None:
    """There's nothing to serve yet -- outside-hours must not turn into
    a permanent 404 just because the scheduler hasn't run once."""
    storage = InMemoryStorage()
    fresh_chain = _chain(datetime.now(UTC))
    provider = _FakeProvider(fresh_chain)
    monkeypatch.setattr("backend.domain.use_cases.read_models.is_market_open", lambda now: False)

    chain = get_option_chain(storage, provider, "SPY")

    assert chain.as_of == fresh_chain.as_of
    assert provider.calls == 1


# get_option_chain_async is GET /chain/{symbol}'s own read path (converted
# from a plain `def` route, 2026-09-22 -- see that route's own comment):
# same freshness/market-hours contract as get_option_chain above, just
# reached via IAsyncMarketReadStorage for the common case instead of a
# thread. Reuses the exact same _chain/_FakeProvider fixtures as every
# test above -- this is the same behavior, just exercised through the
# route's actual code path, not a parallel implementation to keep in sync.


@pytest.mark.asyncio
async def test_async_serves_the_fresh_snapshot_with_no_thread_fallback(monkeypatch) -> None:
    storage = InMemoryStorage()
    fresh_as_of = datetime.now(UTC) - timedelta(seconds=5)
    storage.save_chain_snapshot(_chain(fresh_as_of))
    async_storage = SyncStorageAsyncReadAdapter(storage)
    provider = _FakeProvider(_chain(datetime.now(UTC)))
    monkeypatch.setattr("backend.domain.use_cases.read_models.is_market_open", lambda now: True)

    chain = await get_option_chain_async(async_storage, storage, provider, "SPY")

    assert chain.as_of == fresh_as_of
    assert provider.calls == 0


@pytest.mark.asyncio
async def test_async_falls_through_to_a_thread_when_stale_during_market_hours(monkeypatch) -> None:
    """The one case get_option_chain_async can't do purely on the event
    loop -- IDataProvider has no async-native path (see that function's
    own docstring) -- confirms the fallback still reaches the real
    provider and returns its fresh chain, not just that it doesn't
    crash."""
    storage = InMemoryStorage()
    stale_as_of = datetime.now(UTC) - timedelta(hours=2)
    storage.save_chain_snapshot(_chain(stale_as_of))
    async_storage = SyncStorageAsyncReadAdapter(storage)
    fresh_chain = _chain(datetime.now(UTC))
    provider = _FakeProvider(fresh_chain)
    monkeypatch.setattr("backend.domain.use_cases.read_models.is_market_open", lambda now: True)

    chain = await get_option_chain_async(async_storage, storage, provider, "SPY")

    assert chain.as_of == fresh_chain.as_of
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_async_serves_the_stale_snapshot_without_a_live_call_outside_market_hours(
    monkeypatch,
) -> None:
    storage = InMemoryStorage()
    stale_as_of = datetime.now(UTC) - timedelta(hours=2)
    storage.save_chain_snapshot(_chain(stale_as_of))
    async_storage = SyncStorageAsyncReadAdapter(storage)
    provider = _FakeProvider(_chain(datetime.now(UTC)))
    monkeypatch.setattr("backend.domain.use_cases.read_models.is_market_open", lambda now: False)

    chain = await get_option_chain_async(async_storage, storage, provider, "SPY")

    assert chain.as_of == stale_as_of
    assert provider.calls == 0


@pytest.mark.asyncio
async def test_async_respects_the_expiration_scope(monkeypatch) -> None:
    """The async storage port didn't take an `expiration` filter before
    this route's conversion -- confirms the scoped option-chain-viewer/
    Volatility Smile requests (GET /chain/{symbol}?expiration=X) still
    only ever see that one expiration's snapshot, not a different one
    that happens to be the latest unscoped."""
    storage = InMemoryStorage()
    narrow_expiration = date(2026, 3, 20)
    storage.save_chain_snapshot(_chain(datetime.now(UTC) - timedelta(seconds=5)))
    narrow_chain = OptionChain(
        symbol="SPY",
        as_of=datetime.now(UTC) - timedelta(seconds=5),
        spot_price=Decimal(550),
        contracts=(
            OptionContract(
                underlying="SPY",
                strike=Decimal(560),
                expiration=narrow_expiration,
                contract_type=ContractType.CALL,
                occ_symbol="SPY260320C00560000",
                bid=Decimal(1),
                ask=Decimal("1.10"),
                last=Decimal("1.05"),
                volume=100,
                open_interest=100,
                iv=Decimal("0.20"),
                greeks=Greeks(
                    delta=Decimal("0.50"),
                    gamma=Decimal("0.05"),
                    theta=Decimal("-0.10"),
                    vega=Decimal("0.20"),
                    charm=Decimal("0.01"),
                    vanna=Decimal("0.02"),
                ),
            ),
        ),
    )
    storage.save_chain_snapshot(narrow_chain)
    async_storage = SyncStorageAsyncReadAdapter(storage)
    provider = _FakeProvider(_chain(datetime.now(UTC)))
    monkeypatch.setattr("backend.domain.use_cases.read_models.is_market_open", lambda now: True)

    chain = await get_option_chain_async(
        async_storage, storage, provider, "SPY", expiration=narrow_expiration
    )

    assert {contract.expiration for contract in chain.contracts} == {narrow_expiration}
    assert provider.calls == 0
