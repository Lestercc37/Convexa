"""Volume corrections kept apart from the live counter (migration 0044).

The live counter (contract_cumulative_volume, written by the stream processor) stays continuous: anything that computes CHANGES of volume reads it. A
contract that entered the subscription band late is short by what traded before it was subscribed; a correction pass (backend/volume_correction_pass.py)
asks ThetaData for the day's volume per contract and stores it in contract_volume_corrections. Readers of TOTALS (the /chain reply) take
max(counter, correction) through `apply_volume_corrections`; nothing that computes changes uses it.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

from backend.domain.entities import OptionChain

# The correction pass covers the Tactical window: expirations up to this many calendar days out (0-2 DTE), and the nearest listed one when none falls inside.
CORRECTION_WINDOW_DAYS = 2


def apply_volume_corrections(chain: OptionChain, corrections: dict[str, int]) -> OptionChain:
    """The chain with each contract's volume raised to its correction when that is higher. A contract without a correction, or whose counter is already
    at or above it, is untouched; contracts are never added or removed."""
    if not corrections:
        return chain
    changed = False
    contracts = []
    for contract in chain.contracts:
        corrected = corrections.get(contract.occ_symbol)
        if corrected is not None and corrected > contract.volume:
            contracts.append(replace(contract, volume=corrected))
            changed = True
        else:
            contracts.append(contract)
    return replace(chain, contracts=tuple(contracts)) if changed else chain


def expirations_to_correct(listed: list[date], today: date) -> list[date]:
    """Expirations of one symbol to ask ThetaData about: those within CORRECTION_WINDOW_DAYS of today, or the nearest unexpired one when none is."""
    unexpired = sorted(expiration for expiration in set(listed) if expiration >= today)
    if not unexpired:
        return []
    inside = [expiration for expiration in unexpired if expiration <= today + timedelta(days=CORRECTION_WINDOW_DAYS)]
    return inside or unexpired[:1]
