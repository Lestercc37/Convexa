from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from backend.adapters.storage.memory import InMemoryStorage
from backend.domain.entities import ContractType, OptionChain, OptionContract, OptionGreeks
from backend.domain.use_cases.refresh_snapshot import _merge_cumulative_volume


def _contract(occ_symbol: str, volume: int) -> OptionContract:
    return OptionContract(
        underlying="SPY",
        strike=Decimal(550),
        expiration=date(2026, 3, 20),
        contract_type=ContractType.CALL,
        occ_symbol=occ_symbol,
        bid=Decimal(1),
        ask=Decimal("1.10"),
        last=Decimal("1.05"),
        volume=volume,
        open_interest=100,
        iv=Decimal("0.20"),
        greeks=OptionGreeks(
            delta=Decimal("0.50"),
            gamma=Decimal("0.05"),
            theta=Decimal("-0.10"),
            vega=Decimal("0.20"),
            charm=Decimal("0.01"),
            vanna=Decimal("0.02"),
        ),
    )


def _chain(*contracts: OptionContract) -> OptionChain:
    return OptionChain(
        symbol="SPY",
        as_of=datetime.now(UTC),
        spot_price=Decimal(550),
        contracts=contracts,
    )


def test_replaces_zero_volume_with_a_real_stored_value() -> None:
    """The core case this exists for: a process without its own live
    trade stream (backend/scheduler_worker.py) reports 0 for every
    contract -- StreamStateExporter's own persisted value must be
    picked up instead."""
    storage = InMemoryStorage()
    storage.save_cumulative_volumes({"SPY260320C00550000": 250})
    chain = _chain(_contract("SPY260320C00550000", volume=0))

    merged = _merge_cumulative_volume(chain, storage)

    assert merged.contracts[0].volume == 250


def test_leaves_zero_volume_as_zero_when_storage_has_nothing_for_it() -> None:
    """A genuinely untraded contract -- 0 is the honest answer both
    before and after the merge, not a placeholder to be corrected."""
    storage = InMemoryStorage()
    chain = _chain(_contract("SPY260320C00550000", volume=0))

    merged = _merge_cumulative_volume(chain, storage)

    assert merged.contracts[0].volume == 0


def test_never_overwrites_an_already_nonzero_volume() -> None:
    """A process WITH its own live stream already has the real, current
    value -- a periodic, necessarily-lagged Postgres read of ANOTHER
    process's own snapshot must never clobber it, even if that stored
    value differs."""
    storage = InMemoryStorage()
    storage.save_cumulative_volumes({"SPY260320C00550000": 999})
    chain = _chain(_contract("SPY260320C00550000", volume=42))

    merged = _merge_cumulative_volume(chain, storage)

    assert merged.contracts[0].volume == 42


def test_is_a_no_op_when_every_contract_already_has_real_volume() -> None:
    storage = InMemoryStorage()
    chain = _chain(
        _contract("SPY260320C00550000", volume=10),
        _contract("SPY260320C00555000", volume=20),
    )

    merged = _merge_cumulative_volume(chain, storage)

    assert merged is chain


def test_merges_only_the_zero_volume_contracts_in_a_mixed_chain() -> None:
    storage = InMemoryStorage()
    storage.save_cumulative_volumes({"SPY260320C00555000": 77})
    chain = _chain(
        _contract("SPY260320C00550000", volume=10),
        _contract("SPY260320C00555000", volume=0),
    )

    merged = _merge_cumulative_volume(chain, storage)

    assert merged.contracts[0].volume == 10
    assert merged.contracts[1].volume == 77


def test_scheduler_row_goes_on_top_of_a_fresher_stream_row_and_carries_the_real_volume() -> None:
    from datetime import UTC, datetime, timedelta
    from decimal import Decimal

    from backend.domain.entities import MarketPrice
    from backend.domain.use_cases.refresh_snapshot import scheduler_price_row

    class _Market:
        symbol = "SPY"
        as_of = datetime(2026, 10, 16, 13, 40, 5, tzinfo=UTC)  # option-quote time, a few seconds old
        price = Decimal("771.00")
        volume = 4_237_474

    fresher_stream_row = MarketPrice(
        symbol="SPY", as_of=_Market.as_of + timedelta(seconds=7), price=Decimal("771.40"), volume=488_685
    )

    row = scheduler_price_row(_Market, fresher_stream_row)

    assert row.volume == 4_237_474  # the scheduler's real counter
    assert row.price == Decimal("771.40")  # the fresher price is kept, no stale-price blip
    assert row.as_of == fresher_stream_row.as_of + timedelta(milliseconds=1)  # it is now the latest row

    # nothing fresher stored (first cycle of the day, or an older row): unchanged behavior
    older = MarketPrice(symbol="SPY", as_of=_Market.as_of - timedelta(hours=17), price=Decimal("770"), volume=50_000_000)
    plain = scheduler_price_row(_Market, older)
    assert (plain.as_of, plain.price, plain.volume) == (_Market.as_of, Decimal("771.00"), 4_237_474)
    assert scheduler_price_row(_Market, None).volume == 4_237_474
