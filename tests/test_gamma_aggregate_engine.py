from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from backend.adapters.providers.mock.gamma_aggregate import FakeGammaAggregateCalculator
from backend.adapters.providers.mock.gamma_exposure import FakeGammaExposureCalculator
from backend.domain.entities import (
    ContractType,
    GammaAggregate,
    GammaAggregateItem,
    Greeks,
    OptionChain,
    OptionContract,
)
from backend.domain.ports import IGammaAggregateCalculator
from backend.domain.use_cases import CalculateGammaAggregateUseCase


def test_fake_gamma_aggregate_calculator_groups_gamma_exposure_by_strike() -> None:
    chain = _chain()
    exposures = FakeGammaExposureCalculator().calculate(chain)

    aggregate = FakeGammaAggregateCalculator().calculate(exposures, chain.symbol, chain.as_of)

    assert aggregate == GammaAggregate(
        symbol="SPY",
        as_of=datetime(2026, 1, 15, 14, 30, tzinfo=timezone.utc),
        items=(
            GammaAggregateItem(
                strike=Decimal("540"),
                total_gamma_exposure=Decimal("117975000.000"),
                call_gamma_exposure=Decimal("72600000.000"),
                put_gamma_exposure=Decimal("-45375000.000"),
                net_gamma=Decimal("27225000.000"),
                contract_count=2,
                absolute_gamma=Decimal("27225000.000"),
                open_interest=14000,  # 8000 (call) + 6000 (put) -- P7 fix
                volume=6800,  # 3400 + 3400
            ),
            GammaAggregateItem(
                strike=Decimal("545"),
                total_gamma_exposure=Decimal("63525000.000"),
                call_gamma_exposure=Decimal("60500000.000"),
                put_gamma_exposure=Decimal("-3025000.000"),
                net_gamma=Decimal("57475000.000"),
                contract_count=2,
                absolute_gamma=Decimal("57475000.000"),
                open_interest=6000,  # 5000 (call) + 1000 (put)
                volume=6800,  # 3400 + 3400
            ),
        ),
        total_market_gamma=Decimal("84700000.000"),
        positive_gamma=Decimal("84700000.000"),
        negative_gamma=Decimal("0"),
        total_gamma=Decimal("84700000.000"),
        net_gamma=Decimal("84700000.000"),
        dealer_gamma_notional=Decimal("84700000.000"),
        # 540 wins despite a smaller net_gamma (27.225M vs 545's 57.475M)
        # -- it has far more total two-sided gamma exposure (117.975M vs
        # 63.525M: large offsetting call/put positions, not one-sided).
        # See FakeGammaAggregateCalculator's own comment for why this is
        # the correct ranking now, not a regression.
        absolute_gamma_strike=Decimal("540"),
        peak_gamma_value=Decimal("117975000.000"),
    )


def test_fake_gamma_aggregate_calculator_sums_open_interest_and_volume_per_strike() -> None:
    # P7 fix (2026-09-21): confirmed via full end-to-end trace (OptionContract
    # -> GammaExposure -> GammaAggregateItem) that both fields already existed
    # on GammaAggregateItem, and were already read correctly by every
    # downstream consumer (Walls, storage, the API serializer) -- this
    # calculator was the one place in the chain that never summed them from
    # GammaExposure, so every item silently carried 0 regardless of the real
    # open_interest/volume already present per contract.
    chain = _chain()
    exposures = FakeGammaExposureCalculator().calculate(chain)

    aggregate = FakeGammaAggregateCalculator().calculate(exposures, chain.symbol, chain.as_of)

    by_strike = {item.strike: item for item in aggregate.items}
    assert by_strike[Decimal(540)].open_interest == 14000  # 8000 (call) + 6000 (put)
    assert by_strike[Decimal(540)].volume == 6800  # 3400 + 3400
    assert by_strike[Decimal(545)].open_interest == 6000  # 5000 (call) + 1000 (put)
    assert by_strike[Decimal(545)].volume == 6800  # 3400 + 3400


def test_fake_gamma_aggregate_calculator_selects_peak_by_total_exposure_not_net() -> None:
    # Confirmed live, 2026-09-24: Absolute Gamma Strike must measure total
    # two-sided hedging demand at a strike, not net (directional) gamma --
    # a strike with large, roughly offsetting call/put gamma is exactly
    # the kind of real pinning magnet this level is supposed to surface,
    # and a net-based ranking makes it invisible (nets toward zero) even
    # though it demands more total hedging liquidity than any other
    # strike. Same class of fix already applied to Call Wall/Put Wall
    # (net-gamma-based there -- the opposite correction, since those
    # measure directional dealer positioning, not total exposure).
    chain = _chain()
    exposures = FakeGammaExposureCalculator().calculate(chain)

    aggregate = FakeGammaAggregateCalculator().calculate(exposures, chain.symbol, chain.as_of)

    # 540 has the smaller net_gamma (27.225M vs 545's 57.475M -- 545
    # would win under the old, net-based ranking) but the larger
    # total_gamma_exposure (117.975M vs 63.525M), because 540's call/put
    # legs largely offset (72.6M call, -45.375M put) while 545's are far
    # more one-sided (60.5M call, -3.025M put).
    assert aggregate.items[0].total_gamma_exposure == Decimal("117975000.000")
    assert aggregate.items[1].total_gamma_exposure == Decimal("63525000.000")
    assert aggregate.absolute_gamma_strike == Decimal("540")
    assert aggregate.peak_gamma_value == Decimal("117975000.000")


def test_calculate_gamma_aggregate_use_case_uses_gamma_exposure_output() -> None:
    class RecordingGammaAggregateCalculator:
        def __init__(self) -> None:
            self.received_symbol: str | None = None
            self.received_exposure_count = 0

        def calculate(self, exposures, symbol, as_of) -> GammaAggregate:  # noqa: ANN001
            self.received_symbol = symbol
            self.received_exposure_count = len(exposures)
            return GammaAggregate(symbol=symbol, as_of=as_of)

    calculator: IGammaAggregateCalculator = RecordingGammaAggregateCalculator()
    use_case = CalculateGammaAggregateUseCase(FakeGammaExposureCalculator(), calculator)
    chain = _chain()

    result = use_case.execute(chain)

    assert result.symbol == "SPY"
    assert calculator.received_symbol == "SPY"
    assert calculator.received_exposure_count == len(chain.contracts)


def test_legacy_gamma_aggregate_endpoint_is_removed() -> None:
    from fastapi.testclient import TestClient

    from backend.main import app

    with TestClient(app) as client:
        response = client.post("/options/gamma-aggregate", json=_chain_payload())

    assert response.status_code == 404
    return
    assert response.json() == {
        "schema_version": 1,
        "symbol": "SPY",
        "as_of": "2026-01-15T14:30:00Z",
        "total_market_gamma": 84700000,
        "positive_gamma": 84700000,
        "negative_gamma": 0,
        "absolute_gamma_strike": 545,
        "peak_gamma_value": 57475000,
        "items": [
            {
                "strike": 540,
                "total_gamma_exposure": 117975000,
                "call_gamma_exposure": 72600000,
                "put_gamma_exposure": -45375000,
                "net_gamma": 27225000,
                "contract_count": 2,
                "absolute_gamma": 27225000,
                "open_interest": 0,
                "volume": 0,
            },
            {
                "strike": 545,
                "total_gamma_exposure": 63525000,
                "call_gamma_exposure": 60500000,
                "put_gamma_exposure": -3025000,
                "net_gamma": 57475000,
                "contract_count": 2,
                "absolute_gamma": 57475000,
                "open_interest": 0,
                "volume": 0,
            },
        ],
    }


def _chain() -> OptionChain:
    return OptionChain(
        symbol="SPY",
        as_of=datetime(2026, 1, 15, 14, 30, tzinfo=timezone.utc),
        spot_price=Decimal("550"),
        contracts=(
            _contract(
                ContractType.CALL, "SPY260220C00540000", Decimal("540"), Decimal("0.030"), 8000
            ),
            _contract(
                ContractType.PUT, "SPY260220P00540000", Decimal("540"), Decimal("0.025"), 6000
            ),
            _contract(
                ContractType.CALL, "SPY260220C00545000", Decimal("545"), Decimal("0.040"), 5000
            ),
            _contract(
                ContractType.PUT, "SPY260220P00545000", Decimal("545"), Decimal("0.010"), 1000
            ),
        ),
    )


def _contract(
    contract_type: ContractType,
    occ_symbol: str,
    strike: Decimal,
    gamma: Decimal,
    open_interest: int,
) -> OptionContract:
    return OptionContract(
        underlying="SPY",
        strike=strike,
        expiration=date(2026, 2, 20),
        contract_type=contract_type,
        occ_symbol=occ_symbol,
        bid=Decimal("1.20"),
        ask=Decimal("1.25"),
        last=Decimal("1.22"),
        volume=3400,
        open_interest=open_interest,
        iv=Decimal("0.18"),
        greeks=Greeks(
            delta=Decimal("0"),
            gamma=gamma,
            theta=Decimal("0"),
            vega=Decimal("0"),
            charm=Decimal("0"),
            vanna=Decimal("0"),
        ),
    )


def _chain_payload() -> dict[str, object]:
    return {
        "symbol": "SPY",
        "as_of": "2026-01-15T14:30:00Z",
        "spot_price": 550,
        "contracts": [
            {
                "occ_symbol": c.occ_symbol,
                "underlying": "SPY",
                "strike": float(c.strike),
                "expiration": "2026-02-20",
                "type": c.contract_type.value,
                "bid": 1.2,
                "ask": 1.25,
                "last": 1.22,
                "iv": 0.18,
                "delta": 0,
                "gamma": float(c.greeks.gamma),
                "theta": 0,
                "vega": 0,
                "charm": 0,
                "vanna": 0,
                "open_interest": c.open_interest,
                "volume": 3400,
            }
            for c in _chain().contracts
        ],
    }
