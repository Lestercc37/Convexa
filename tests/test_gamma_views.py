from __future__ import annotations

from dataclasses import replace
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


def test_execute_both_builds_only_the_tactical_aggregate_and_leaves_structural_to_execute() -> None:
    """Since 2026-10-16 the refresh cycle (execute_both) builds and persists ONLY the Tactical
    (fixed 0-2 DTE) aggregate -- no Structural row is written. execute() (backfill scripts,
    manual triggers) still builds the Structural one from the same chain: SPY's 30-day tier
    sees the contracts outside the tactical window, Tactical does not."""
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

    tactical = orchestrator.execute_both("SPY")

    # execute_both persisted nothing structural
    assert storage.get_latest_gamma_aggregate("SPY", view="structural") is None
    structural = orchestrator.execute("SPY")

    assert structural.view == "structural"
    assert tactical.view == "tactical"
    structural_strikes = {item.strike for item in structural.items}
    tactical_strikes = {item.strike for item in tactical.items}
    assert structural_strikes == {Decimal(550), Decimal(560), Decimal(540)}
    assert tactical_strikes == {Decimal(550)}
    assert tactical_strikes < structural_strikes

    saved_structural = storage.get_latest_gamma_aggregate("SPY", view="structural")
    saved_tactical = storage.get_latest_gamma_aggregate("SPY", view="tactical")
    assert saved_structural is not None and saved_tactical is not None
    assert {item.strike for item in saved_structural.items} == structural_strikes
    assert {item.strike for item in saved_tactical.items} == tactical_strikes


def test_execute_both_skips_tactical_for_a_symbol_outside_the_enabled_scope() -> None:
    """AAPL is not in TACTICAL_ENABLED_SYMBOLS (indices, the four liquid
    ETFs, and NVDA -- see that constant's own comment) -- confirmed live,
    2026-09-29, that this scope had never actually been enforced in code,
    and every one of the 15 active symbols was getting a full Tactical
    build (BSM greeks, 3 aggregations, walls, max pain, 5 Greek
    exposures, plus its own two Postgres writes) every single scheduler
    cycle, unthrottled. execute_both() must still return and persist an
    honest-empty Tactical aggregate for AAPL (same has_data=False shape
    the frontend already handles for a real 0-2 DTE gap), not skip
    persisting entirely (which would leave get_latest_gamma_aggregate()
    returning a permanently stale pre-fix row). No Structural row is built
    for AAPL any more (Structural retired 2026-10-16)."""
    storage = InMemoryStorage()
    contracts = (
        _contract(
            "AAPL260115C00550000", ContractType.CALL, Decimal(550), TODAY_ET,
            open_interest=100, gamma="0.02",
        ),
        _contract(
            "AAPL260115P00550000", ContractType.PUT, Decimal(550), TODAY_ET,
            open_interest=100, gamma="0.02",
        ),
    )
    chain = OptionChain(symbol="AAPL", as_of=AS_OF, spot_price=SPOT_PRICE, contracts=contracts)
    storage.save_chain_snapshot(chain)
    orchestrator = _orchestrator(storage)

    tactical = orchestrator.execute_both("AAPL")

    assert storage.get_latest_gamma_aggregate("AAPL", view="structural") is None
    assert tactical.view == "tactical"
    assert tactical.items == ()
    assert tactical.gamma_flip is None
    assert tactical.call_wall is None

    saved_tactical = storage.get_latest_gamma_aggregate("AAPL", view="tactical")
    assert saved_tactical is not None
    assert saved_tactical.items == ()


def test_execute_tactical_alone_also_respects_the_enabled_scope() -> None:
    """The single-view manual-trigger entry point (see execute_tactical's
    own docstring) must behave the same as execute_both() for an
    out-of-scope symbol -- an honest-empty result, not an error, and not
    a full computation."""
    storage = InMemoryStorage()
    contracts = (
        _contract(
            "TSLA260115C00550000", ContractType.CALL, Decimal(550), TODAY_ET,
            open_interest=100, gamma="0.02",
        ),
    )
    chain = OptionChain(symbol="TSLA", as_of=AS_OF, spot_price=SPOT_PRICE, contracts=contracts)
    storage.save_chain_snapshot(chain)
    orchestrator = _orchestrator(storage)

    tactical = orchestrator.execute_tactical("TSLA")

    assert tactical.view == "tactical"
    assert tactical.items == ()


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


def test_execute_both_rebuilds_tactical_every_call_and_never_writes_structural() -> None:
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

    first = orchestrator.execute_both("SPY")

    second_as_of = AS_OF + timedelta(minutes=5)
    storage.save_chain_snapshot(
        OptionChain(symbol="SPY", as_of=second_as_of, spot_price=SPOT_PRICE, contracts=contracts)
    )
    second = orchestrator.execute_both("SPY")

    assert first.as_of == AS_OF and first.view == "tactical" and first.items != ()
    assert second.as_of == second_as_of
    assert storage.get_latest_gamma_aggregate("SPY", view="structural") is None




def test_execute_both_also_saves_one_view_per_listed_expiration_and_leaves_tactical_unchanged() -> None:
    """The dashboard tabs 0DTE | 1DTE | 2DTE (2026-10-26): each reads ONE listed expiration, by listed order, while the 0-2 DTE
    (tactical) aggregate is built exactly as before. Today's, tomorrow's and a listing 5 days out (the third LISTED one, outside
    0-2 calendar days, like Thursday -> Monday)."""
    storage = InMemoryStorage()
    in_five_days = TODAY_ET + timedelta(days=5)
    tomorrow = TODAY_ET + timedelta(days=1)
    contracts = (
        _contract("SPY260115C00550000", ContractType.CALL, Decimal(550), TODAY_ET, open_interest=100, gamma="0.02"),
        _contract("SPY260115P00550000", ContractType.PUT, Decimal(550), TODAY_ET, open_interest=100, gamma="0.02"),
        _contract("SPY260116C00552000", ContractType.CALL, Decimal(552), tomorrow, open_interest=100, gamma="0.01"),
        _contract("SPY260120P00548000", ContractType.PUT, Decimal(548), in_five_days, open_interest=100, gamma="0.01"),
    )
    storage.save_chain_snapshot(OptionChain(symbol="SPY", as_of=AS_OF, spot_price=SPOT_PRICE, contracts=contracts))

    tactical = _orchestrator(storage).execute_both("SPY")

    assert {item.strike for item in tactical.items} == {Decimal(550), Decimal(552)}
    strikes = {}
    for view in ("0dte", "1dte", "2dte"):
        saved = storage.get_latest_gamma_aggregate("SPY", view=view)
        assert saved is not None and saved.view == view
        strikes[view] = {item.strike for item in saved.items}
    assert strikes == {"0dte": {Decimal(550)}, "1dte": {Decimal(552)}, "2dte": {Decimal(548)}}


def test_a_missing_expiration_slot_is_an_honest_empty_view() -> None:
    storage = InMemoryStorage()
    contracts = (
        _contract("SPY260115C00550000", ContractType.CALL, Decimal(550), TODAY_ET, open_interest=100, gamma="0.02"),
    )
    storage.save_chain_snapshot(OptionChain(symbol="SPY", as_of=AS_OF, spot_price=SPOT_PRICE, contracts=contracts))

    _orchestrator(storage).execute_both("SPY")

    second = storage.get_latest_gamma_aggregate("SPY", view="1dte")
    assert second is not None and second.items == ()


def test_the_0dte_volume_view_weights_contracts_by_todays_volume_instead_of_open_interest() -> None:
    """"OI | Volumen" (2026-10-26): the same expiration and formulas, the traded volume in the open interest's place. A strike with big open interest
    but no trades today weighs nothing in the volume view."""
    storage = InMemoryStorage()
    quiet = replace(
        _contract("SPY260115C00550000", ContractType.CALL, Decimal(550), TODAY_ET, open_interest=5000, gamma="0.02"), volume=0
    )
    busy = replace(
        _contract("SPY260115C00555000", ContractType.CALL, Decimal(555), TODAY_ET, open_interest=10, gamma="0.02"), volume=800
    )
    storage.save_chain_snapshot(OptionChain(symbol="SPY", as_of=AS_OF, spot_price=SPOT_PRICE, contracts=(quiet, busy)))

    _orchestrator(storage).execute_both("SPY")

    by_oi = {item.strike: item for item in storage.get_latest_gamma_aggregate("SPY", view="0dte").items}
    by_volume = {item.strike: item for item in storage.get_latest_gamma_aggregate("SPY", view="0dte_volume").items}
    assert by_oi[Decimal(550)].call_gamma_exposure > by_oi[Decimal(555)].call_gamma_exposure
    assert by_volume[Decimal(550)].call_gamma_exposure == 0
    assert by_volume[Decimal(555)].call_gamma_exposure > 0
