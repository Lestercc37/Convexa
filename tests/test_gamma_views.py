from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from backend.adapters.providers.mock.gamma_aggregate import FakeGammaAggregateCalculator
from backend.adapters.providers.mock.gamma_exposure import FakeGammaExposureCalculator
from backend.adapters.providers.mock.gamma_flip import FakeGammaFlipCalculator
from backend.adapters.providers.mock.max_pain import FakeMaxPainCalculator
from backend.adapters.providers.mock.walls import FakeWallCalculator
from backend.adapters.providers.thetadata.greeks import PassthroughGreeksCalculator
from backend.adapters.storage.memory import InMemoryStorage
from backend.domain.entities import ContractType, Greeks, OptionChain, OptionContract
from backend.domain.use_cases import (
    CalculateGammaAggregateUseCase,
    CalculateGammaExposureOrchestrator,
    CalculateGammaFlipUseCase,
    CalculateGreeksUseCase,
    CalculateMaxPainUseCase,
    CalculateWallsUseCase,
)

# 2026-01-15 14:30 UTC is 09:30 ET (EST, UTC-5) -- expiration=date(2026,1,15)
# is genuinely "today" in the market's own timezone, same fixture anchor
# test_walls_exclude_0dte.py already uses.
AS_OF = datetime(2026, 1, 15, 14, 30, tzinfo=UTC)
TODAY_ET = date(2026, 1, 15)
SPOT_PRICE = Decimal(550)


def _orchestrator(storage: InMemoryStorage) -> CalculateGammaExposureOrchestrator:
    return CalculateGammaExposureOrchestrator(
        storage=storage,
        greeks=CalculateGreeksUseCase(PassthroughGreeksCalculator()),
        aggregate=CalculateGammaAggregateUseCase(
            FakeGammaExposureCalculator(), FakeGammaAggregateCalculator()
        ),
        gamma_flip=CalculateGammaFlipUseCase(FakeGammaFlipCalculator()),
        walls=CalculateWallsUseCase(FakeWallCalculator()),
        max_pain=CalculateMaxPainUseCase(FakeMaxPainCalculator()),
    )


def _contract(
    occ_symbol: str,
    contract_type: ContractType,
    strike: Decimal,
    expiration: date,
    open_interest: int,
    gamma: str,
) -> OptionContract:
    return OptionContract(
        underlying="SPY",
        strike=strike,
        expiration=expiration,
        contract_type=contract_type,
        occ_symbol=occ_symbol,
        bid=Decimal(1),
        ask=Decimal("1.10"),
        last=Decimal("1.05"),
        volume=100,
        open_interest=open_interest,
        iv=Decimal("0.20"),
        greeks=Greeks(
            delta=Decimal("0.50") if contract_type == ContractType.CALL else Decimal("-0.50"),
            gamma=Decimal(gamma),
            theta=Decimal("-0.10"),
            vega=Decimal("0.20"),
            charm=Decimal("0.01"),
            vanna=Decimal("0.01"),
        ),
    )


def test_execute_both_produces_independently_scoped_structural_and_tactical_aggregates() -> None:
    """The core of this feature: execute_both() must not just return the
    same result twice with a different label -- Structural (SPY's own
    30-day tier) and Tactical (fixed 0-2 DTE) genuinely see different
    contract sets when a real one sits outside the tactical window but
    inside the structural one."""
    storage = InMemoryStorage()
    contracts = (
        _contract(
            "SPY260115C00550000", ContractType.CALL, Decimal(550), TODAY_ET,
            open_interest=100, gamma="0.02",
        ),
        _contract(
            "SPY260115P00550000", ContractType.PUT, Decimal(550), TODAY_ET,
            open_interest=100, gamma="0.02",
        ),
        # 20 days out -- inside SPY's 30-day structural window, outside
        # the fixed 2-day tactical one.
        _contract(
            "SPY260204C00560000", ContractType.CALL, Decimal(560), TODAY_ET + timedelta(days=20),
            open_interest=100, gamma="0.01",
        ),
        _contract(
            "SPY260204P00540000", ContractType.PUT, Decimal(540), TODAY_ET + timedelta(days=20),
            open_interest=100, gamma="0.01",
        ),
    )
    chain = OptionChain(symbol="SPY", as_of=AS_OF, spot_price=SPOT_PRICE, contracts=contracts)
    storage.save_chain_snapshot(chain)
    orchestrator = _orchestrator(storage)

    structural, tactical = orchestrator.execute_both("SPY")

    assert structural.view == "structural"
    assert tactical.view == "tactical"
    structural_strikes = {item.strike for item in structural.items}
    tactical_strikes = {item.strike for item in tactical.items}
    assert structural_strikes == {Decimal(550), Decimal(560), Decimal(540)}
    assert tactical_strikes == {Decimal(550)}
    assert tactical_strikes < structural_strikes

    # Both are actually persisted, independently.
    assert storage.get_latest_gamma_aggregate("SPY", view="structural") is not None
    assert storage.get_latest_gamma_aggregate("SPY", view="tactical") is not None
    saved_structural = storage.get_latest_gamma_aggregate("SPY", view="structural")
    saved_tactical = storage.get_latest_gamma_aggregate("SPY", view="tactical")
    assert saved_structural is not None and saved_tactical is not None
    assert {item.strike for item in saved_structural.items} == structural_strikes
    assert {item.strike for item in saved_tactical.items} == tactical_strikes


def test_tactical_walls_include_0dte_unlike_structural() -> None:
    """Structural excludes 0DTE from wall selection (see
    test_walls_exclude_0dte.py) because a single expiring-today strike's
    BSM gamma divergence would hijack an otherwise-stable multi-day
    level. Tactical is the opposite: 0DTE IS the signal it exists to
    measure, so its own wall selection must NOT exclude it."""
    storage = InMemoryStorage()
    contracts = (
        # 0DTE, dominant gamma, one-sided per strike (a call-only strike
        # and a put-only strike, not both on the same strike -- wall
        # selection is net-gamma-based, so a call and a put sharing a
        # strike would cancel each other's net_gamma toward zero
        # regardless of how large each leg's own exposure is, which
        # would defeat the point of this fixture). Must win Tactical's
        # own walls.
        _contract(
            "SPY260115C00550000", ContractType.CALL, Decimal(550), TODAY_ET,
            open_interest=100, gamma="1.0",
        ),
        _contract(
            "SPY260115P00545000", ContractType.PUT, Decimal(545), TODAY_ET,
            open_interest=100, gamma="1.0",
        ),
        # 1DTE, weak gamma -- still inside the 0-2 day tactical window, so
        # this is what a bug that excluded 0DTE from Tactical would fall
        # back to instead.
        _contract(
            "SPY260116C00551000", ContractType.CALL, Decimal(551), TODAY_ET + timedelta(days=1),
            open_interest=100, gamma="0.01",
        ),
        _contract(
            "SPY260116P00549000", ContractType.PUT, Decimal(549), TODAY_ET + timedelta(days=1),
            open_interest=100, gamma="0.01",
        ),
    )
    chain = OptionChain(symbol="SPY", as_of=AS_OF, spot_price=SPOT_PRICE, contracts=contracts)
    storage.save_chain_snapshot(chain)
    orchestrator = _orchestrator(storage)

    tactical = orchestrator.execute_tactical("SPY")

    assert tactical.call_wall == Decimal(550)
    assert tactical.put_wall == Decimal(545)


def test_execute_tactical_falls_back_to_nearest_listed_expiration_when_0_2_dte_is_empty() -> None:
    """Reversed 2026-09-25 (see TACTICAL_FALLBACK_WINDOW_DAYS' own
    comment): real listed-expiration data pulled from the DB the same day
    showed VIX/DIA/ES genuinely go empty under the strict 0-2 DTE window
    on a meaningful fraction of trading days -- an individual stock that
    only lists Friday weeklies, on a day that isn't within 2 real
    calendar days of one, must now fall back to that nearest listing
    instead of showing nothing."""
    storage = InMemoryStorage()
    contracts = (
        _contract(
            "SPY260204C00560000", ContractType.CALL, Decimal(560), TODAY_ET + timedelta(days=20),
            open_interest=100, gamma="0.01",
        ),
        _contract(
            "SPY260204P00540000", ContractType.PUT, Decimal(540), TODAY_ET + timedelta(days=20),
            open_interest=100, gamma="0.01",
        ),
    )
    chain = OptionChain(symbol="SPY", as_of=AS_OF, spot_price=SPOT_PRICE, contracts=contracts)
    storage.save_chain_snapshot(chain)
    orchestrator = _orchestrator(storage)

    tactical = orchestrator.execute_tactical("SPY")

    assert tactical.view == "tactical"
    assert tactical.items != ()
    assert {item.strike for item in tactical.items} == {Decimal(560), Decimal(540)}
    assert tactical.call_wall is not None
    assert tactical.put_wall is not None


def test_execute_tactical_with_no_chain_contracts_at_all_is_an_honest_empty_result() -> None:
    """The only remaining honest-empty case: nothing has been fetched
    for this symbol yet at all (not even a nearest listed expiration to
    fall back to)."""
    storage = InMemoryStorage()
    chain = OptionChain(symbol="SPY", as_of=AS_OF, spot_price=SPOT_PRICE, contracts=())
    storage.save_chain_snapshot(chain)
    orchestrator = _orchestrator(storage)

    tactical = orchestrator.execute_tactical("SPY")

    assert tactical.view == "tactical"
    assert tactical.items == ()
    assert tactical.call_wall is None
    assert tactical.put_wall is None
    assert tactical.gamma_flip is None


def test_structural_is_not_rebuilt_before_the_refresh_interval_elapses() -> None:
    """Added 2026-09-25 per the user's own trading style (a scalper/day
    trader reads Tactical as their working set, Structural as slower
    background macro context) -- STRUCTURAL_REFRESH_INTERVAL throttles
    Structural's own rebuild+persist, Tactical stays on the full
    scheduler cadence every call."""
    storage = InMemoryStorage()
    contracts = (
        _contract(
            "SPY260115C00550000", ContractType.CALL, Decimal(550), TODAY_ET,
            open_interest=100, gamma="0.02",
        ),
        _contract(
            "SPY260115P00550000", ContractType.PUT, Decimal(550), TODAY_ET,
            open_interest=100, gamma="0.02",
        ),
    )
    storage.save_chain_snapshot(
        OptionChain(symbol="SPY", as_of=AS_OF, spot_price=SPOT_PRICE, contracts=contracts)
    )
    orchestrator = _orchestrator(storage)

    first_structural, first_tactical = orchestrator.execute_both("SPY")

    second_as_of = AS_OF + timedelta(minutes=5)
    storage.save_chain_snapshot(
        OptionChain(symbol="SPY", as_of=second_as_of, spot_price=SPOT_PRICE, contracts=contracts)
    )

    second_structural, second_tactical = orchestrator.execute_both("SPY")

    # Structural is exactly the row from the first call -- not rebuilt.
    assert second_structural.as_of == first_structural.as_of == AS_OF
    # Tactical is never throttled -- rebuilt every call.
    assert second_tactical.as_of == second_as_of
    assert second_tactical.as_of != first_tactical.as_of
    # And no second structural row silently landed in storage either.
    persisted_structural = storage.get_latest_gamma_aggregate("SPY", view="structural")
    assert persisted_structural is not None
    assert persisted_structural.as_of == AS_OF


def test_structural_is_rebuilt_once_the_refresh_interval_elapses() -> None:
    storage = InMemoryStorage()
    contracts = (
        _contract(
            "SPY260115C00550000", ContractType.CALL, Decimal(550), TODAY_ET,
            open_interest=100, gamma="0.02",
        ),
        _contract(
            "SPY260115P00550000", ContractType.PUT, Decimal(550), TODAY_ET,
            open_interest=100, gamma="0.02",
        ),
    )
    storage.save_chain_snapshot(
        OptionChain(symbol="SPY", as_of=AS_OF, spot_price=SPOT_PRICE, contracts=contracts)
    )
    orchestrator = _orchestrator(storage)

    first_structural, _ = orchestrator.execute_both("SPY")

    later_as_of = AS_OF + timedelta(minutes=16)
    storage.save_chain_snapshot(
        OptionChain(symbol="SPY", as_of=later_as_of, spot_price=SPOT_PRICE, contracts=contracts)
    )

    second_structural, second_tactical = orchestrator.execute_both("SPY")

    assert second_structural.as_of == later_as_of
    assert second_structural.as_of != first_structural.as_of
    assert second_tactical.as_of == later_as_of


def test_structural_always_builds_fresh_on_the_first_call_for_a_symbol() -> None:
    # No prior persisted structural row -- existing_structural is None,
    # must never be mistaken for "fresh enough to skip".
    storage = InMemoryStorage()
    contracts = (
        _contract(
            "SPY260115C00550000", ContractType.CALL, Decimal(550), TODAY_ET,
            open_interest=100, gamma="0.02",
        ),
        _contract(
            "SPY260115P00550000", ContractType.PUT, Decimal(550), TODAY_ET,
            open_interest=100, gamma="0.02",
        ),
    )
    storage.save_chain_snapshot(
        OptionChain(symbol="SPY", as_of=AS_OF, spot_price=SPOT_PRICE, contracts=contracts)
    )
    orchestrator = _orchestrator(storage)

    structural, _ = orchestrator.execute_both("SPY")

    assert structural.as_of == AS_OF
    assert structural.items != ()
