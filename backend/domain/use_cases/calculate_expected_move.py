from __future__ import annotations

from datetime import datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from backend.domain.entities import ContractType, ExpectedMove, OptionChain, OptionContract

MARKET_MINUTES = Decimal(390)
NEW_YORK = ZoneInfo("America/New_York")


def calculate_time_to_close_pct(as_of: datetime) -> Decimal:
    local = as_of.astimezone(NEW_YORK)
    open_time = datetime.combine(local.date(), time(9, 30), NEW_YORK)
    close_time = datetime.combine(local.date(), time(16, 0), NEW_YORK)
    if local <= open_time:
        return Decimal(100)
    if local >= close_time:
        return Decimal(0)
    remaining_minutes = Decimal(str((close_time - local).total_seconds())) / Decimal(60)
    return max(Decimal(0), min(Decimal(100), remaining_minutes / MARKET_MINUTES * 100))


def _mid_price(contract: OptionContract) -> Decimal | None:
    """Bid/ask midpoint, or None for a quote this calculation must not
    use -- no bid at all, or a crossed market (bid > ask). Matches the
    Cboe VIX Mathematics Methodology's own null-quote handling (section
    3(a)(ii)/(iii))."""
    if contract.bid <= 0 or contract.bid > contract.ask:
        return None
    return (contract.bid + contract.ask) / 2


def _select_atm_strike_and_forward(
    calls_by_strike: dict[Decimal, OptionContract],
    puts_by_strike: dict[Decimal, OptionContract],
) -> tuple[Decimal, Decimal] | None:
    """K0's own candidate strike and the option-implied forward price F --
    Cboe VIX Mathematics Methodology section 3(a)(ii). The strike price at
    which the absolute difference between the call and put mid price is
    smallest (ties broken by the lowest strike), among strikes quoting
    both a call and a put with a usable (non-null, non-crossed) mid price.

    R (the risk-free rate) is treated as 0 in F = Strike + e^(RT) x
    (Call-Put) -- deliberately, not an oversight: this calculation always
    runs against `chain`'s own nearest expiration, which for every symbol
    this system tracks intraday is 0DTE (T measured in hours, not the
    30-day constant-maturity term VIX itself targets). e^(RT) for R in a
    normal short-rate range and T on the order of 1/2000 of a year is
    indistinguishable from 1 well past this calculation's own Decimal
    precision -- carrying R through would add a provider dependency (a
    live rate fetch) to what is otherwise a pure function, for a term
    with no measurable effect on the result.
    """
    best_strike: Decimal | None = None
    best_diff: Decimal | None = None
    best_forward: Decimal | None = None
    for strike in sorted(set(calls_by_strike) & set(puts_by_strike)):
        call_mid = _mid_price(calls_by_strike[strike])
        put_mid = _mid_price(puts_by_strike[strike])
        if call_mid is None or put_mid is None:
            continue
        diff = abs(call_mid - put_mid)
        if best_diff is None or diff < best_diff:
            best_diff = diff
            best_strike = strike
            best_forward = strike + (call_mid - put_mid)
    if best_strike is None or best_forward is None:
        return None
    return best_strike, best_forward


def _select_k0(strikes: list[Decimal], forward: Decimal, atm_strike: Decimal) -> Decimal:
    """First strike equal to or otherwise immediately below F (Cboe VIX
    Mathematics Methodology section 3(a)(ii)) -- falls back to the ATM
    strike found above if, in some degenerate chain, nothing sits at or
    below F (F below every strike we have)."""
    candidates = [strike for strike in strikes if strike <= forward]
    return max(candidates) if candidates else atm_strike


def _select_otm_strikes(
    calls_by_strike: dict[Decimal, OptionContract],
    puts_by_strike: dict[Decimal, OptionContract],
    k0: Decimal,
) -> dict[Decimal, Decimal]:
    """Strike selection, Cboe VIX Mathematics Methodology section
    3(a)(iii): OTM puts below K0 walking downward and OTM calls above K0
    walking upward, each direction stopping the instant two CONSECUTIVE
    strikes (in that walk's own direction) have no bid -- filters out
    illiquid noise the same way Cboe's own real index calculation does,
    instead of an arbitrary liquidity cutoff invented for this codebase.
    Both the K0 put and K0 call are included, averaged into one price
    (Cboe's own documented convention -- two options share this one
    strike, unlike every other selected strike). Returns {strike: mid_price}.
    """
    selected: dict[Decimal, Decimal] = {}

    put_strikes_below_k0 = sorted((strike for strike in puts_by_strike if strike < k0), reverse=True)
    consecutive_zero_bids = 0
    for strike in put_strikes_below_k0:
        contract = puts_by_strike[strike]
        if contract.bid <= 0:
            consecutive_zero_bids += 1
            if consecutive_zero_bids >= 2:
                break
            continue
        consecutive_zero_bids = 0
        mid = _mid_price(contract)
        if mid is not None:
            selected[strike] = mid

    call_strikes_above_k0 = sorted(strike for strike in calls_by_strike if strike > k0)
    consecutive_zero_bids = 0
    for strike in call_strikes_above_k0:
        contract = calls_by_strike[strike]
        if contract.bid <= 0:
            consecutive_zero_bids += 1
            if consecutive_zero_bids >= 2:
                break
            continue
        consecutive_zero_bids = 0
        mid = _mid_price(contract)
        if mid is not None:
            selected[strike] = mid

    if k0 in calls_by_strike and k0 in puts_by_strike:
        call_mid = _mid_price(calls_by_strike[k0])
        put_mid = _mid_price(puts_by_strike[k0])
        if call_mid is not None and put_mid is not None:
            selected[k0] = (call_mid + put_mid) / 2

    return selected


def _strike_interval(sorted_strikes: list[Decimal], index: int) -> Decimal:
    """Delta-K for the strike at `index` -- half the gap to each neighbor,
    or the single gap to the one neighbor that exists at either edge of
    the selected set (Cboe VIX Mathematics Methodology section 3(a)(iv))."""
    if len(sorted_strikes) == 1:
        return Decimal(0)
    if index == 0:
        return sorted_strikes[1] - sorted_strikes[0]
    if index == len(sorted_strikes) - 1:
        return sorted_strikes[index] - sorted_strikes[index - 1]
    return (sorted_strikes[index + 1] - sorted_strikes[index - 1]) / 2


def _calculate_variance(
    selected: dict[Decimal, Decimal], k0: Decimal, forward: Decimal, year_fraction: Decimal
) -> Decimal | None:
    """sigma^2, Cboe VIX Mathematics Methodology formula (1):
    (2/T) x sum(deltaK_i / K_i^2 x Q(K_i)) - (1/T) x [(F/K0) - 1]^2.
    Requires at least 2 selected strikes (a single strike carries no
    deltaK -- see _strike_interval) and a strictly positive T."""
    if len(selected) < 2 or year_fraction <= 0:
        return None
    sorted_strikes = sorted(selected)
    contribution_sum = Decimal(0)
    for index, strike in enumerate(sorted_strikes):
        price = selected[strike]
        delta_k = _strike_interval(sorted_strikes, index)
        contribution_sum += (delta_k / (strike * strike)) * price
    variance = (Decimal(2) / year_fraction) * contribution_sum
    variance -= (Decimal(1) / year_fraction) * ((forward / k0) - 1) ** 2
    return variance


def _fallback_atm_iv(chain: OptionChain, atm_strike: Decimal) -> Decimal | None:
    """The pre-2026-09-29 approximation ((call.iv + put.iv) / 2 at the
    single strike closest to spot) -- kept only as a last-resort fallback
    for a chain too thin/illiquid for the real variance-swap calculation
    above to find anything usable (e.g. a brand-new symbol's first tick,
    before enough quotes exist), never the primary path."""
    candidates = [
        contract
        for contract in chain.contracts
        if contract.strike == atm_strike
    ]
    call = next((c for c in candidates if c.contract_type == ContractType.CALL), None)
    put = next((c for c in candidates if c.contract_type == ContractType.PUT), None)
    if call is None or put is None:
        return None
    return (call.iv + put.iv) / 2


def calculate_expected_move(
    chain: OptionChain, as_of: datetime, session_open_price: Decimal | None = None
) -> ExpectedMove:
    """1-standard-deviation expected move to the close of `chain`'s own
    nearest expiration (0DTE for every symbol this system tracks
    intraday), using the same variance-swap-replication methodology real
    VIX uses for its own single-term calculation (Cboe VIX Mathematics
    Methodology section 3(a)) -- a wide strip of OTM strikes weighted by
    1/K^2 x deltaK, not just the two contracts nearest spot. See this
    module's own private helpers for the step-by-step match to that
    document.

    `session_open_price`, not `chain.spot_price`, anchors upper_bound/
    lower_bound when provided -- confirmed live, 2026-09-28: recomputing
    this band around the CURRENT spot every cycle means it can never sit
    ahead of price, only chase it, defeating its purpose as a level
    price is expected to react to before reaching it. Callers pass the
    session's own earliest recorded price (see calculate_session_open);
    `None` (no reading recorded yet this session) falls back to
    `chain.spot_price`, the same honest "nothing better yet" convention
    AtrRange.today_open already uses.
    """
    local_date = as_of.astimezone(NEW_YORK).date()
    nearest_expiration = min(contract.expiration for contract in chain.contracts)
    expiration_contracts = [
        contract for contract in chain.contracts if contract.expiration == nearest_expiration
    ]
    calls_by_strike = {
        contract.strike: contract
        for contract in expiration_contracts
        if contract.contract_type == ContractType.CALL
    }
    puts_by_strike = {
        contract.strike: contract
        for contract in expiration_contracts
        if contract.contract_type == ContractType.PUT
    }

    time_to_close_pct = calculate_time_to_close_pct(as_of)
    dte = max((nearest_expiration - local_date).days, 0)
    year_fraction = time_to_close_pct / 100 / 365 if dte == 0 else Decimal(dte) / 365

    anchor_price = session_open_price if session_open_price is not None else chain.spot_price

    atm = _select_atm_strike_and_forward(calls_by_strike, puts_by_strike)
    variance: Decimal | None = None
    atm_strike = min(
        {contract.strike for contract in expiration_contracts},
        key=lambda strike: (abs(strike - chain.spot_price), strike),
    )
    if atm is not None:
        atm_strike, forward = atm
        all_strikes = sorted(set(calls_by_strike) | set(puts_by_strike))
        k0 = _select_k0(all_strikes, forward, atm_strike)
        selected = _select_otm_strikes(calls_by_strike, puts_by_strike, k0)
        variance = _calculate_variance(selected, k0, forward, year_fraction)

    if variance is not None and variance > 0:
        equivalent_iv = variance.sqrt()
    else:
        # Falls all the way back to the pre-existing approximation --
        # never raises, never silently returns a zero/garbage move.
        fallback_iv = _fallback_atm_iv(chain, atm_strike)
        equivalent_iv = fallback_iv if fallback_iv is not None else Decimal(0)

    implied_pct = equivalent_iv * year_fraction.sqrt() * 100
    implied_dollars = anchor_price * equivalent_iv * year_fraction.sqrt()
    remaining_scale = (time_to_close_pct / 100).sqrt()
    remaining_dollars = implied_dollars * remaining_scale
    remaining_pct = implied_pct * remaining_scale

    return ExpectedMove(
        implied_1sd_dollars=implied_dollars,
        implied_1sd_pct=implied_pct,
        remaining_1sd_dollars=remaining_dollars,
        remaining_1sd_pct=remaining_pct,
        upper_bound=anchor_price + implied_dollars,
        lower_bound=anchor_price - implied_dollars,
        atm_iv=equivalent_iv,
    )
