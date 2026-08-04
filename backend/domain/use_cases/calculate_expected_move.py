from __future__ import annotations

from datetime import datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from backend.domain.entities import ContractType, ExpectedMove, OptionChain

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


def calculate_expected_move(chain: OptionChain, as_of: datetime) -> ExpectedMove:
    local_date = as_of.astimezone(NEW_YORK).date()
    nearest_expiration = min(contract.expiration for contract in chain.contracts)
    expiration_contracts = [
        contract for contract in chain.contracts if contract.expiration == nearest_expiration
    ]
    atm_strike = min(
        {contract.strike for contract in expiration_contracts},
        key=lambda strike: (abs(strike - chain.spot_price), strike),
    )
    call = next(
        contract
        for contract in expiration_contracts
        if contract.strike == atm_strike and contract.contract_type == ContractType.CALL
    )
    put = next(
        contract
        for contract in expiration_contracts
        if contract.strike == atm_strike and contract.contract_type == ContractType.PUT
    )
    atm_iv = (call.iv + put.iv) / 2
    time_to_close_pct = calculate_time_to_close_pct(as_of)
    dte = max((nearest_expiration - local_date).days, 0)
    year_fraction = time_to_close_pct / 100 / 365 if dte == 0 else Decimal(dte) / 365
    implied_dollars = chain.spot_price * atm_iv * year_fraction.sqrt()
    implied_pct = implied_dollars / chain.spot_price * 100
    remaining_scale = (time_to_close_pct / 100).sqrt()
    remaining_dollars = implied_dollars * remaining_scale
    remaining_pct = implied_pct * remaining_scale
    return ExpectedMove(
        implied_1sd_dollars=implied_dollars,
        implied_1sd_pct=implied_pct,
        remaining_1sd_dollars=remaining_dollars,
        remaining_1sd_pct=remaining_pct,
        upper_bound=chain.spot_price + implied_dollars,
        lower_bound=chain.spot_price - implied_dollars,
        atm_iv=atm_iv,
    )
