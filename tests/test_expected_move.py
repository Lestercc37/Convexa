from datetime import UTC, date, datetime
from decimal import Decimal

from backend.domain.entities import ContractType, Greeks, OptionChain, OptionContract
from backend.domain.use_cases import calculate_expected_move, calculate_time_to_close_pct


def _contract(
    strike: Decimal,
    contract_type: ContractType,
    bid: Decimal,
    ask: Decimal,
    expiration: date = date(2026, 8, 5),
    iv: Decimal = Decimal("0.25"),
) -> OptionContract:
    return OptionContract(
        underlying="SPY",
        strike=strike,
        expiration=expiration,
        contract_type=contract_type,
        occ_symbol=f"SPY260805{contract_type.value}{strike}",
        bid=bid,
        ask=ask,
        last=(bid + ask) / 2,
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


def test_variance_matches_an_independently_computed_reference_value() -> None:
    """3-strike chain (95 put, 100 call+put, 105 call) with clean, hand-
    picked bid/ask -- call/put mid are equal at 100, so that's both the
    ATM strike and K0, and the forward-price correction term
    [(F/K0)-1]^2 is exactly 0, isolating the sum-of-contributions term.
    The expected value below was computed independently (a standalone
    script re-implementing formula (1) directly against this same data,
    not by calling calculate_expected_move itself) -- see this test's own
    PR for that script's output. Two days to expiration (not 0DTE) keeps
    year_fraction a clean 2/365, sidestepping calculate_time_to_close_pct
    entirely for this specific check.
    """
    as_of = datetime(2026, 8, 3, 14, 0, tzinfo=UTC)  # 10:00 ET
    chain = OptionChain(
        symbol="SPY",
        as_of=as_of,
        spot_price=Decimal("100.5"),  # deliberately not exactly K0 -- must not leak into the variance
        contracts=(
            _contract(Decimal(95), ContractType.PUT, Decimal("0.90"), Decimal("1.10")),
            _contract(Decimal(100), ContractType.CALL, Decimal("2.90"), Decimal("3.10")),
            _contract(Decimal(100), ContractType.PUT, Decimal("2.90"), Decimal("3.10")),
            _contract(Decimal(105), ContractType.CALL, Decimal("0.90"), Decimal("1.10")),
        ),
    )

    result = calculate_expected_move(chain, as_of)

    expected_atm_iv = Decimal("0.9152489463005885641421850365").sqrt()
    assert result.atm_iv == expected_atm_iv

    year_fraction = Decimal(2) / Decimal(365)
    expected_dollars = chain.spot_price * expected_atm_iv * year_fraction.sqrt()
    assert result.implied_1sd_dollars == expected_dollars
    assert result.upper_bound == chain.spot_price + expected_dollars
    assert result.lower_bound == chain.spot_price - expected_dollars


def test_session_open_price_anchors_the_band_instead_of_live_spot() -> None:
    # Same chain as above (same variance, same atm_iv) but spot has moved
    # intraday to 110 -- confirmed live, 2026-09-28: recomputing the band
    # around whatever spot currently is means it can never sit ahead of
    # price. Passing session_open_price must anchor upper_bound/
    # lower_bound to THAT price, not the live one on `chain`.
    as_of = datetime(2026, 8, 3, 14, 0, tzinfo=UTC)
    chain = OptionChain(
        symbol="SPY",
        as_of=as_of,
        spot_price=Decimal(110),
        contracts=(
            _contract(Decimal(95), ContractType.PUT, Decimal("0.90"), Decimal("1.10")),
            _contract(Decimal(100), ContractType.CALL, Decimal("2.90"), Decimal("3.10")),
            _contract(Decimal(100), ContractType.PUT, Decimal("2.90"), Decimal("3.10")),
            _contract(Decimal(105), ContractType.CALL, Decimal("0.90"), Decimal("1.10")),
        ),
    )
    session_open_price = Decimal(100)

    result = calculate_expected_move(chain, as_of, session_open_price=session_open_price)

    expected_atm_iv = Decimal("0.9152489463005885641421850365").sqrt()
    year_fraction = Decimal(2) / Decimal(365)
    expected_dollars = session_open_price * expected_atm_iv * year_fraction.sqrt()
    assert result.implied_1sd_dollars == expected_dollars
    assert result.upper_bound == session_open_price + expected_dollars
    assert result.lower_bound == session_open_price - expected_dollars
    # The live spot (110) must never leak into the anchor.
    assert result.upper_bound != Decimal(110) + expected_dollars


def test_without_session_open_price_falls_back_to_the_chains_own_spot() -> None:
    as_of = datetime(2026, 8, 3, 14, 0, tzinfo=UTC)
    chain = OptionChain(
        symbol="SPY",
        as_of=as_of,
        spot_price=Decimal(100),
        contracts=(
            _contract(Decimal(95), ContractType.PUT, Decimal("0.90"), Decimal("1.10")),
            _contract(Decimal(100), ContractType.CALL, Decimal("2.90"), Decimal("3.10")),
            _contract(Decimal(100), ContractType.PUT, Decimal("2.90"), Decimal("3.10")),
            _contract(Decimal(105), ContractType.CALL, Decimal("0.90"), Decimal("1.10")),
        ),
    )

    result = calculate_expected_move(chain, as_of)

    assert result.upper_bound == chain.spot_price + result.implied_1sd_dollars


def test_strike_selection_stops_after_two_consecutive_zero_bid_strikes() -> None:
    # Puts at 90 and 85 have no bid (back to back) -- Cboe's own strike-
    # selection rule (section 3(a)(iii)) says stop there and never
    # consider 80 either, even though 80 itself has a real bid.
    as_of = datetime(2026, 8, 3, 14, 0, tzinfo=UTC)
    chain = OptionChain(
        symbol="SPY",
        as_of=as_of,
        spot_price=Decimal(100),
        contracts=(
            _contract(Decimal(80), ContractType.PUT, Decimal("0.05"), Decimal("0.15")),
            _contract(Decimal(85), ContractType.PUT, Decimal(0), Decimal("0.10")),
            _contract(Decimal(90), ContractType.PUT, Decimal(0), Decimal("0.20")),
            _contract(Decimal(95), ContractType.PUT, Decimal("0.90"), Decimal("1.10")),
            _contract(Decimal(100), ContractType.CALL, Decimal("2.90"), Decimal("3.10")),
            _contract(Decimal(100), ContractType.PUT, Decimal("2.90"), Decimal("3.10")),
            _contract(Decimal(105), ContractType.CALL, Decimal("0.90"), Decimal("1.10")),
        ),
    )

    with_gap = calculate_expected_move(chain, as_of)

    # Removing the two zero-bid strikes and the now-unreachable 80 strike
    # entirely must produce the IDENTICAL result -- proof they were never
    # actually included.
    chain_without_excluded_strikes = OptionChain(
        symbol="SPY",
        as_of=as_of,
        spot_price=Decimal(100),
        contracts=tuple(
            contract for contract in chain.contracts if contract.strike not in (Decimal(80), Decimal(85), Decimal(90))
        ),
    )
    without_gap = calculate_expected_move(chain_without_excluded_strikes, as_of)

    assert with_gap.atm_iv == without_gap.atm_iv


def test_falls_back_to_atm_iv_average_when_the_chain_is_too_thin_to_select_strikes() -> None:
    # Only one strike in the whole chain -- no deltaK is possible
    # (_strike_interval needs at least 2 selected strikes), so this must
    # fall back to the old (call.iv + put.iv) / 2 approximation instead
    # of raising or returning a zero/garbage move.
    as_of = datetime(2026, 8, 3, 14, 0, tzinfo=UTC)
    chain = OptionChain(
        symbol="SPY",
        as_of=as_of,
        spot_price=Decimal(100),
        contracts=(
            _contract(Decimal(100), ContractType.CALL, Decimal("2.90"), Decimal("3.10"), iv=Decimal("0.20")),
            _contract(Decimal(100), ContractType.PUT, Decimal("2.90"), Decimal("3.10"), iv=Decimal("0.30")),
        ),
    )

    result = calculate_expected_move(chain, as_of)

    assert result.atm_iv == Decimal("0.25")


def test_time_to_close_pct_unchanged() -> None:
    as_of = datetime(2026, 8, 3, 14, 0, tzinfo=UTC)  # 10:00 ET
    time_fraction = Decimal(360) / Decimal(390)
    assert calculate_time_to_close_pct(as_of) == time_fraction * 100
