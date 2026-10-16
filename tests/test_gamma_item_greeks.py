from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from backend.adapters.providers.mock.gamma_aggregate import FakeGammaAggregateCalculator
from backend.adapters.providers.mock.gamma_exposure import FakeGammaExposureCalculator
from backend.api.serializers import gamma_aggregate_response
from backend.domain.entities import (
    ContractType,
    GammaAggregate,
    GammaAggregateItem,
    InvalidOptionError,
    OptionChain,
    OptionContract,
    OptionGreeks,
)

SPOT = Decimal(7800)
AS_OF = datetime(2026, 10, 15, 14, 30, tzinfo=UTC)


def contract(contract_type: ContractType, strike: int, *, delta: str, vanna: str, charm: str, oi: int) -> OptionContract:
    right = "C" if contract_type == ContractType.CALL else "P"
    return OptionContract(
        underlying="SPX",
        strike=Decimal(strike),
        expiration=date(2026, 10, 15),
        contract_type=contract_type,
        occ_symbol=f"SPXW261015{right}{strike * 1000:08d}",
        bid=Decimal(10),
        ask=Decimal("10.5"),
        last=Decimal("10.25"),
        volume=5,
        open_interest=oi,
        iv=Decimal("0.15"),
        greeks=OptionGreeks(
            delta=Decimal(delta),
            gamma=Decimal("0.01"),
            theta=Decimal(-1),
            vega=Decimal(2),
            charm=Decimal(charm),
            vanna=Decimal(vanna),
        ),
    )


CALL = contract(ContractType.CALL, 7800, delta="0.5", vanna="-0.02", charm="0.3", oi=1000)
PUT = contract(ContractType.PUT, 7800, delta="-0.4", vanna="-0.01", charm="0.2", oi=500)
OTHER_STRIKE_CALL = contract(ContractType.CALL, 7850, delta="0.2", vanna="0.05", charm="-0.1", oi=200)


def exposures(*contracts: OptionContract):
    chain = OptionChain(symbol="SPX", as_of=AS_OF, spot_price=SPOT, contracts=tuple(contracts))
    return FakeGammaExposureCalculator().calculate(chain)


def test_the_exposure_of_a_contract_is_greek_times_oi_times_100_times_spot() -> None:
    call, put = exposures(CALL, PUT)

    notional_call = Decimal(100) * 1000 * SPOT
    notional_put = Decimal(100) * 500 * SPOT
    assert call.delta_exposure == Decimal("0.5") * notional_call
    assert put.delta_exposure == Decimal("-0.4") * notional_put  # delta keeps its own sign: puts are negative
    # vanna and charm take the dealer sign of the GEX: call +1, put -1
    assert call.vanna_exposure == Decimal("-0.02") * notional_call
    assert put.vanna_exposure == -Decimal("-0.01") * notional_put
    assert call.charm_exposure == Decimal("0.3") * notional_call
    assert put.charm_exposure == -Decimal("0.2") * notional_put


def test_the_aggregate_sums_calls_and_puts_of_each_strike() -> None:
    aggregate = FakeGammaAggregateCalculator().calculate(exposures(CALL, PUT, OTHER_STRIKE_CALL), "SPX", AS_OF)

    by_strike = {item.strike: item for item in aggregate.items}
    at_7800 = by_strike[Decimal(7800)]
    assert at_7800.delta_exposure == Decimal("0.5") * 100 * 1000 * SPOT + Decimal("-0.4") * 100 * 500 * SPOT
    assert at_7800.vanna_exposure == Decimal("-0.02") * 100 * 1000 * SPOT - Decimal("-0.01") * 100 * 500 * SPOT
    assert at_7800.charm_exposure == Decimal("0.3") * 100 * 1000 * SPOT - Decimal("0.2") * 100 * 500 * SPOT
    assert by_strike[Decimal(7850)].delta_exposure == Decimal("0.2") * 100 * 200 * SPOT


def test_an_item_without_the_new_exposures_means_not_stored_not_zero() -> None:
    item = GammaAggregateItem(
        strike=Decimal(7800),
        total_gamma_exposure=Decimal(1),
        call_gamma_exposure=Decimal(1),
        put_gamma_exposure=Decimal(0),
        net_gamma=Decimal(1),
        contract_count=1,
    )

    assert item.delta_exposure is None
    assert item.vanna_exposure is None
    assert item.charm_exposure is None


@pytest.mark.parametrize("field", ["delta_exposure", "vanna_exposure", "charm_exposure"])
def test_a_non_finite_exposure_is_rejected(field: str) -> None:
    with pytest.raises(InvalidOptionError):
        GammaAggregateItem(
            strike=Decimal(7800),
            total_gamma_exposure=Decimal(1),
            call_gamma_exposure=Decimal(1),
            put_gamma_exposure=Decimal(0),
            net_gamma=Decimal(1),
            contract_count=1,
            **{field: Decimal("NaN")},
        )


def test_the_profile_response_carries_the_exposures_and_null_for_old_rows() -> None:
    stored = GammaAggregateItem(
        strike=Decimal(7800),
        total_gamma_exposure=Decimal(1),
        call_gamma_exposure=Decimal(1),
        put_gamma_exposure=Decimal(0),
        net_gamma=Decimal(1),
        contract_count=2,
        delta_exposure=Decimal("123.5"),
        vanna_exposure=Decimal(-4),
        charm_exposure=Decimal(7),
    )
    legacy = GammaAggregateItem(
        strike=Decimal(7805),
        total_gamma_exposure=Decimal(1),
        call_gamma_exposure=Decimal(1),
        put_gamma_exposure=Decimal(0),
        net_gamma=Decimal(1),
        contract_count=1,
    )

    response = gamma_aggregate_response(GammaAggregate(symbol="SPX", as_of=AS_OF, items=(stored, legacy)))

    new_row, old_row = response["items"]
    assert (new_row["delta_exposure"], new_row["vanna_exposure"], new_row["charm_exposure"]) == (123.5, -4, 7)
    assert (old_row["delta_exposure"], old_row["vanna_exposure"], old_row["charm_exposure"]) == (None, None, None)
