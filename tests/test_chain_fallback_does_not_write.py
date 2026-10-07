"""GET /chain/{symbol}'s live fallback must not write option_chain_snapshots.

Found 2026-10-07: the API process has no trade stream, so its provider reports
volume 0 for every contract, and the fallback used to save that chain -- 138 of
289 SPXW 0DTE snapshots on 10-07 (48% of rows) were all-zero-volume. The scheduler
is now the only writer; the fallback returns the live chain with the volume read
back from contract_cumulative_volume (the stream processor's export)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from backend.adapters.providers.mock.provider import MockDataProvider
from backend.adapters.storage.memory import InMemoryStorage
from backend.adapters.storage.sync_read_adapter import SyncStorageAsyncReadAdapter
from backend.api.serializers import chain_response
from backend.domain.entities import ContractType, Greeks, OptionChain, OptionContract
from backend.domain.use_cases import read_models
from backend.domain.use_cases.read_models import (
    CHAIN_STORED_MAX_AGE_SECONDS,
    get_option_chain,
    get_option_chain_async,
)
from backend.main import app

TRADED = "SPXW261007C07800000"
UNTRADED = "SPXW261007P07800000"


def _contract(occ_symbol: str, strike: int, contract_type: ContractType, volume: int = 0) -> OptionContract:
    return OptionContract(
        underlying="SPX",
        strike=Decimal(strike),
        expiration=date(2026, 10, 7),
        contract_type=contract_type,
        occ_symbol=occ_symbol,
        bid=Decimal("6.70"),
        ask=Decimal("6.80"),
        last=Decimal("6.75"),
        volume=volume,
        open_interest=2429,
        iv=Decimal("0.1199"),
        greeks=Greeks(
            delta=Decimal("0.49"),
            gamma=Decimal("0.01"),
            theta=Decimal("-1.0"),
            vega=Decimal("0.5"),
            charm=Decimal("0.01"),
            vanna=Decimal("0.02"),
        ),
    )


def _chain(as_of: datetime, volume: int = 0) -> OptionChain:
    return OptionChain(
        symbol="SPX",
        as_of=as_of,
        spot_price=Decimal("7800.5"),
        contracts=(
            _contract(TRADED, 7800, ContractType.CALL, volume),
            _contract(UNTRADED, 7800, ContractType.PUT, volume),
        ),
    )


class _CountingStorage(InMemoryStorage):
    def __init__(self) -> None:
        super().__init__()
        self.saves = 0

    def save_chain_snapshot(self, chain: OptionChain) -> None:
        self.saves += 1
        super().save_chain_snapshot(chain)


class _ZeroVolumeProvider:
    """What the API process's provider returns: live quotes, volume 0 everywhere."""

    def __init__(self) -> None:
        self.calls = 0

    def get_option_chain(self, underlying: str, expiration: date | None = None) -> OptionChain:
        self.calls += 1
        return _chain(datetime.now(UTC), volume=0)


@pytest.fixture
def market_open(monkeypatch) -> None:
    monkeypatch.setattr("backend.domain.use_cases.read_models.is_market_open", lambda now: True)


def _stale_storage() -> tuple[_CountingStorage, datetime]:
    storage = _CountingStorage()
    stale_as_of = datetime.now(UTC) - timedelta(seconds=CHAIN_STORED_MAX_AGE_SECONDS + 240)
    storage.save_chain_snapshot(_chain(stale_as_of, volume=111))   # the scheduler's own write
    storage.saves = 0
    return storage, stale_as_of


def test_threshold_is_a_named_constant_kept_at_60_seconds() -> None:
    assert CHAIN_STORED_MAX_AGE_SECONDS == 60
    assert read_models.CHAIN_STORED_MAX_AGE_SECONDS is CHAIN_STORED_MAX_AGE_SECONDS


def test_old_chain_goes_live_but_writes_no_rows(market_open) -> None:
    storage, stale_as_of = _stale_storage()
    provider = _ZeroVolumeProvider()

    chain = get_option_chain(storage, provider, "SPX", date(2026, 10, 7))

    assert provider.calls == 1
    assert storage.saves == 0
    latest = storage.get_latest_chain_snapshot("SPX")
    assert latest is not None and latest.as_of == stale_as_of       # still only the scheduler's snapshot
    assert chain.as_of > stale_as_of                                 # but the reply is the live chain


def test_returned_volume_matches_the_stored_cumulative_volume(market_open) -> None:
    storage, _ = _stale_storage()
    storage.save_cumulative_volumes({TRADED: 140_548})
    provider = _ZeroVolumeProvider()

    chain = get_option_chain(storage, provider, "SPX", date(2026, 10, 7))
    volume = {c.occ_symbol: c.volume for c in chain.contracts}

    assert volume[TRADED] == 140_548          # the processor's export, not the hub's 0
    assert volume[UNTRADED] == 0              # no record -> an honest 0, never invented
    assert storage.get_cumulative_volumes([TRADED, UNTRADED]) == {TRADED: 140_548}


def test_fresh_stored_chain_is_still_served_without_a_live_call(market_open) -> None:
    storage = _CountingStorage()
    fresh = datetime.now(UTC) - timedelta(seconds=5)
    storage.save_chain_snapshot(_chain(fresh, volume=7))
    storage.saves = 0
    provider = _ZeroVolumeProvider()

    chain = get_option_chain(storage, provider, "SPX")

    assert provider.calls == 0 and storage.saves == 0
    assert chain.as_of == fresh


@pytest.mark.asyncio
async def test_async_route_path_writes_nothing_and_fills_the_volume(market_open) -> None:
    storage, stale_as_of = _stale_storage()
    storage.save_cumulative_volumes({TRADED: 90_423})
    provider = _ZeroVolumeProvider()

    chain = await get_option_chain_async(
        SyncStorageAsyncReadAdapter(storage), storage, provider, "SPX", date(2026, 10, 7)
    )

    assert provider.calls == 1 and storage.saves == 0
    assert {c.occ_symbol: c.volume for c in chain.contracts}[TRADED] == 90_423


def test_volatility_smile_still_gets_its_data_from_the_route(market_open) -> None:
    """The Smile only needs strike + iv per contract of one expiration."""
    with TestClient(app) as client:
        storage = app.state.container.storage
        stale = MockDataProvider().get_option_chain("SPY")
        stale = OptionChain(
            symbol=stale.symbol,
            as_of=datetime.now(UTC) - timedelta(minutes=10),
            spot_price=stale.spot_price,
            contracts=stale.contracts,
        )
        storage.save_chain_snapshot(stale)
        expiration = stale.contracts[0].expiration

        response = client.get(f"/api/v1/chain/spy?expiration={expiration.isoformat()}")
        written = list(storage._chains.get("SPY", []))

    assert response.status_code == 200
    contracts = response.json()["contracts"]
    assert contracts and all({"strike", "iv"} <= contract.keys() for contract in contracts)
    assert {c["expiration"] for c in contracts} == {expiration.isoformat()}
    assert [chain.as_of for chain in written] == [stale.as_of]       # the route wrote nothing
    assert chain_response(stale)["symbol"] == "SPY"
