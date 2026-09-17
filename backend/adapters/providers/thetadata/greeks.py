from __future__ import annotations

from backend.domain.entities import OptionChain
from backend.domain.ports import IGreeksCalculator


class PassthroughGreeksCalculator(IGreeksCalculator):
    """No-op IGreeksCalculator for chains that already carry real Greeks.

    ThetaDataProvider.get_option_chain() already attaches real Greeks to
    every OptionContract before returning it -- real delta/theta/vega
    quotes from ThetaData, gamma/vanna/charm via calculate_bsm_greeks
    with real IV (see that provider's own comments). Wiring
    FakeGreeksCalculator downstream of it in CalculateGammaExposure
    Orchestrator silently overwrote those real values with deliberately
    synthetic ones for every derived metric (GEX, DEX, VEX, TEX, Net
    GEX, walls, Gamma Flip, Max Pain) -- confirmed live, 2026-09-17, by
    matching a real chain's persisted call_gamma_exposure exactly
    against FakeGreeksCalculator's formula rather than the real BSM
    gamma ThetaDataProvider had already computed for the same contract.
    This calculator is the fix: the chain it's given already has the
    Greeks it needs, so there's nothing left to calculate.
    """

    def calculate(self, chain: OptionChain) -> OptionChain:
        return chain
