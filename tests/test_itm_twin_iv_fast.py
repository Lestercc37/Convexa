"""The OTM-twin IV rule (PR #320) in the 10-second fast layer: a noisy in-the-money quote is re-priced with the IV of its OTM twin, exactly like the REST cycle does, so the 10-second columns do not fall
back to the noisy IV the cycle already replaced. Default off (same switch as the provider's: Settings.thetadata_itm_iv_from_twin)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from backend.adapters.providers.thetadata.provider import _is_noisy_itm_quote as provider_is_noisy
from backend.adapters.providers.thetadata.provider import _needs_twin_iv as provider_needs_twin_iv
from backend.adapters.storage.memory import InMemoryStorage
from backend.domain.entities import ContractType, Greeks, OptionContract
from backend.domain.use_cases.fast_gamma import (
    FastGammaLayer,
    is_noisy_itm_quote,
    needs_twin_iv,
    twin_iv_by_contract,
)
from backend.domain.use_cases.market_hours import EASTERN_TIME
from tests.test_gamma_views import _orchestrator

EXPIRATION = date(2026, 1, 15)
NOW_ET = datetime(2026, 1, 15, 10, 0, 30, tzinfo=EASTERN_TIME)
RATE = Decimal("0.04")
SPOT = Decimal(550)


def _tte(expiration: date, quote_time_et: datetime) -> Decimal:
    return Decimal(6) / Decimal(24 * 365)


def _contract(kind: ContractType, strike: int, bid: str, ask: str, iv: str) -> OptionContract:
    letter = "C" if kind == ContractType.CALL else "P"
    return OptionContract(
        underlying="SPY", strike=Decimal(strike), expiration=EXPIRATION, contract_type=kind, occ_symbol=f"SPY260115{letter}00{strike}000",
        bid=Decimal(bid), ask=Decimal(ask), last=Decimal(bid), volume=10, open_interest=1000, iv=Decimal(iv),
        greeks=Greeks(delta=Decimal("0.5"), gamma=Decimal("0.001"), theta=Decimal("-0.1"), vega=Decimal("0.2"), charm=Decimal("0.01"), vanna=Decimal("0.01")),
    )


# A deep-ITM call at 540 (spot 550): intrinsic 10.00, mid 10.03, spread 0.20 -> time value 0.03 < half the spread 0.10 = noisy. Its twin, the 540 put, is OTM with a readable IV.
NOISY_CALL = _contract(ContractType.CALL, 540, "9.93", "10.13", "0.35")
TWIN_PUT = _contract(ContractType.PUT, 540, "0.05", "0.07", "0.22")
READABLE_ITM_CALL = _contract(ContractType.CALL, 545, "7.00", "7.20", "0.25")  # time value 2.10 vs half spread 0.10: readable


def test_the_noisy_itm_rule_matches_the_providers_and_covers_calls_and_puts() -> None:
    grid = [(kind, strike, bid, ask) for kind in (ContractType.CALL, ContractType.PUT) for strike in (500, 545, 549, 550, 551, 555, 600)
            for bid, ask in (("0.01", "0.03"), ("5.00", "5.20"), ("10.00", "10.20"), ("0", "0.05"), ("3", "2"))]
    for kind, strike, bid, ask in grid:
        expected = provider_is_noisy("CALL" if kind == ContractType.CALL else "PUT", Decimal(strike), SPOT, Decimal(bid), Decimal(ask))
        assert is_noisy_itm_quote(kind, Decimal(strike), SPOT, Decimal(bid), Decimal(ask)) is expected, (kind, strike, bid, ask)
    assert is_noisy_itm_quote(ContractType.CALL, Decimal(540), SPOT, Decimal("9.93"), Decimal("10.13"))
    assert not is_noisy_itm_quote(ContractType.PUT, Decimal(540), SPOT, Decimal("0.05"), Decimal("0.07"))  # OTM
    assert not is_noisy_itm_quote(ContractType.CALL, Decimal(545), SPOT, Decimal("7.00"), Decimal("7.20"))  # time value is readable


def test_twin_iv_map_keeps_only_readable_positive_iv_contracts() -> None:
    zero_iv = _contract(ContractType.PUT, 545, "0.10", "0.12", "0")
    twins = twin_iv_by_contract((NOISY_CALL, TWIN_PUT, READABLE_ITM_CALL, zero_iv), SPOT)
    assert twins == {
        (EXPIRATION, Decimal(540), ContractType.PUT): Decimal("0.22"),
        (EXPIRATION, Decimal(545), ContractType.CALL): Decimal("0.25"),
    }


def _layer(twin: bool) -> FastGammaLayer:
    storage = InMemoryStorage()
    return FastGammaLayer(storage, _orchestrator(storage), time_to_expiration=_tte, risk_free_rate=lambda: RATE, itm_iv_from_twin=twin)


def test_a_noisy_itm_contract_is_re_priced_with_its_twin_iv_only_when_the_switch_is_on() -> None:
    chain = (NOISY_CALL, TWIN_PUT)
    spot = SPOT
    off = _layer(False)._reprice(NOISY_CALL, spot, SPOT, RATE, NOW_ET, None)
    on_layer = _layer(True)
    twin_iv = twin_iv_by_contract(chain, SPOT)
    on = on_layer._reprice(NOISY_CALL, spot, SPOT, RATE, NOW_ET, twin_iv)
    plain_twin = _layer(False)._reprice(
        _contract(ContractType.CALL, 540, "9.93", "10.13", "0.22"), spot, SPOT, RATE, NOW_ET, None
    )
    assert on.greeks.gamma == plain_twin.greeks.gamma  # priced with 0.22, the twin's IV
    assert on.greeks.gamma != off.greeks.gamma  # not with the noisy 0.35
    assert on.iv == Decimal("0.35")  # the contract's own IV field is untouched


def test_readable_contracts_and_the_twin_itself_are_priced_with_their_own_iv() -> None:
    chain = (NOISY_CALL, TWIN_PUT, READABLE_ITM_CALL)
    twin_iv = twin_iv_by_contract(chain, SPOT)
    layer = _layer(True)
    for contract in (TWIN_PUT, READABLE_ITM_CALL):
        assert layer._reprice(contract, SPOT, SPOT, RATE, NOW_ET, twin_iv).greeks == _layer(False)._reprice(contract, SPOT, SPOT, RATE, NOW_ET, None).greeks


def test_a_noisy_contract_without_a_twin_keeps_its_own_iv() -> None:
    twin_iv = twin_iv_by_contract((NOISY_CALL,), SPOT)  # the 540 put is missing
    with_switch = _layer(True)._reprice(NOISY_CALL, SPOT, SPOT, RATE, NOW_ET, twin_iv)
    without = _layer(False)._reprice(NOISY_CALL, SPOT, SPOT, RATE, NOW_ET, None)
    assert with_switch.greeks == without.greeks


# 2026-10-23 15:21 ET: ThetaData answers implied_vol 0 for a deep-ITM 0DTE quote whose mid is at or below intrinsic (every SPX put 7650-7700 with spot 7629.6), so its gamma
# was 0 and the net gamma above the price looked call-only. Here a 560 put with spot 550: intrinsic 10.00, mid 9.95 (below intrinsic), IV 0; its twin, the 560 call, is OTM.
ZERO_IV_PUT = _contract(ContractType.PUT, 560, "9.90", "10.00", "0")
TWIN_CALL = _contract(ContractType.CALL, 560, "0.05", "0.07", "0.21")


def test_needs_twin_iv_matches_the_provider_and_includes_itm_quotes_without_iv() -> None:
    grid = [(kind, strike, bid, ask, iv) for kind in (ContractType.CALL, ContractType.PUT) for strike in (500, 545, 550, 555, 600)
            for bid, ask in (("0.01", "0.03"), ("5.00", "5.20"), ("9.90", "10.00"), ("3", "2")) for iv in ("0", "0.2")]
    for kind, strike, bid, ask, iv in grid:
        expected = provider_needs_twin_iv("CALL" if kind == ContractType.CALL else "PUT", Decimal(strike), SPOT, Decimal(bid), Decimal(ask), Decimal(iv))
        assert needs_twin_iv(kind, Decimal(strike), SPOT, Decimal(bid), Decimal(ask), Decimal(iv)) is expected, (kind, strike, bid, ask, iv)
    assert needs_twin_iv(ContractType.PUT, Decimal(560), SPOT, Decimal("9.90"), Decimal("10.00"), Decimal(0))
    assert not needs_twin_iv(ContractType.PUT, Decimal(540), SPOT, Decimal("0.05"), Decimal("0.07"), Decimal(0))  # OTM without IV: no twin, stays without gamma


def test_an_itm_contract_without_iv_gets_its_twins_iv_and_a_real_gamma() -> None:
    twin_iv = twin_iv_by_contract((ZERO_IV_PUT, TWIN_CALL), SPOT)
    on = _layer(True)._reprice(ZERO_IV_PUT, SPOT, SPOT, RATE, NOW_ET, twin_iv)
    priced_with_twin = _layer(False)._reprice(_contract(ContractType.PUT, 560, "9.90", "10.00", "0.21"), SPOT, SPOT, RATE, NOW_ET, None)
    assert on.greeks.gamma > 0
    assert on.greeks.gamma == priced_with_twin.greeks.gamma
    assert on.iv == Decimal(0)  # the stored IV field is still ThetaData's
    off = _layer(False)._reprice(ZERO_IV_PUT, SPOT, SPOT, RATE, NOW_ET, None)
    assert off is ZERO_IV_PUT  # switch off: unchanged, as before
    no_twin = _layer(True)._reprice(ZERO_IV_PUT, SPOT, SPOT, RATE, NOW_ET, twin_iv_by_contract((ZERO_IV_PUT,), SPOT))
    assert no_twin is ZERO_IV_PUT  # no twin available: unchanged
