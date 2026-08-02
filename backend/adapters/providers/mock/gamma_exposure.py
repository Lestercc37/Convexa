from __future__ import annotations

from decimal import Decimal

from backend.domain.entities import ContractType, GammaExposure, OptionChain, OptionContract
from backend.domain.ports import IGammaExposureCalculator


class FakeGammaExposureCalculator(IGammaExposureCalculator):
    """Deterministic per-contract Gamma Exposure calculator.

    It applies the documented per-one-percent-move dealer GEX formula.
    """

    def calculate(self, chain: OptionChain) -> tuple[GammaExposure, ...]:
        return tuple(
            self._calculate_contract_exposure(contract, chain.spot_price)
            for contract in chain.contracts
        )

    def _calculate_contract_exposure(
        self, contract: OptionContract, spot_price: Decimal
    ) -> GammaExposure:
        sign = Decimal("1") if contract.contract_type == ContractType.CALL else Decimal("-1")
        dealer_gamma_exposure = (
            contract.greeks.gamma
            * Decimal(contract.open_interest)
            * Decimal("100")
            * spot_price**2
            * Decimal("0.01")
            * sign
        )
        return GammaExposure(
            occ_symbol=contract.occ_symbol,
            strike=contract.strike,
            contract_type=contract.contract_type,
            expiration=contract.expiration,
            gamma=contract.greeks.gamma,
            open_interest=contract.open_interest,
            dealer_gamma_exposure=dealer_gamma_exposure,
            sign=sign,
        )
