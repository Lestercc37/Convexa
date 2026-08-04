from datetime import UTC, date, datetime
from decimal import Decimal

from backend.domain.entities import ContractType, Greeks, OptionChain, OptionContract
from backend.domain.use_cases import calculate_expected_move, calculate_time_to_close_pct


def test_expected_move_uses_atm_straddle_iv_and_zero_dte_session_fraction() -> None:
    as_of = datetime(2026, 8, 3, 14, 0, tzinfo=UTC)  # 10:00 ET
    chain = OptionChain(
        symbol="SPY",
        as_of=as_of,
        spot_price=Decimal(100),
        contracts=(
            _contract(Decimal(99), ContractType.CALL, Decimal("0.20")),
            _contract(Decimal(99), ContractType.PUT, Decimal("0.30")),
            _contract(Decimal(105), ContractType.CALL, Decimal("0.80")),
            _contract(Decimal(105), ContractType.PUT, Decimal("0.90")),
        ),
    )

    result = calculate_expected_move(chain, as_of)

    time_fraction = Decimal(360) / Decimal(390)
    expected_dollars = Decimal(100) * Decimal("0.25") * (time_fraction / Decimal(365)).sqrt()
    assert calculate_time_to_close_pct(as_of) == time_fraction * 100
    assert result.atm_iv == Decimal("0.25")
    assert result.implied_1sd_dollars == expected_dollars
    assert result.implied_1sd_pct == expected_dollars
    assert result.remaining_1sd_dollars == expected_dollars * time_fraction.sqrt()
    assert result.remaining_1sd_pct == expected_dollars * time_fraction.sqrt()
    assert result.upper_bound == Decimal(100) + expected_dollars
    assert result.lower_bound == Decimal(100) - expected_dollars


def _contract(strike: Decimal, contract_type: ContractType, iv: Decimal) -> OptionContract:
    return OptionContract(
        underlying="SPY",
        strike=strike,
        expiration=date(2026, 8, 3),
        contract_type=contract_type,
        occ_symbol=f"SPY260803{contract_type.value}{strike}",
        bid=Decimal(1),
        ask=Decimal("1.1"),
        last=Decimal("1.05"),
        volume=1,
        open_interest=1,
        iv=iv,
        greeks=Greeks(
            delta=Decimal("0.5"),
            gamma=Decimal("0.01"),
            theta=Decimal("-0.01"),
            vega=Decimal("0.1"),
            charm=Decimal(0),
            vanna=Decimal(0),
        ),
    )
