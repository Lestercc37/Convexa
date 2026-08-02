from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal

from backend.domain.entities import ContractType, Greeks, OptionChain
from backend.domain.ports import IGreeksCalculator


class FakeGreeksCalculator(IGreeksCalculator):
    """Deterministic Greeks calculator for architecture validation.

    This is deliberately not Black-Scholes. Values vary predictably with
    moneyness and days to expiration so distinct contracts do not collapse to
    identical Greeks.
    """

    def calculate(self, chain: OptionChain) -> OptionChain:
        contracts = tuple(
            replace(
                contract,
                greeks=self._greeks_for(
                    contract.contract_type,
                    contract.strike,
                    chain.spot_price,
                    contract.expiration,
                    chain.as_of.date(),
                ),
            )
            for contract in chain.contracts
        )
        return replace(chain, contracts=contracts)

    def _greeks_for(
        self,
        contract_type: ContractType,
        strike: Decimal,
        spot_price: Decimal,
        expiration: date,
        as_of: date,
    ) -> Greeks:
        dte = max((expiration - as_of).days, 1)
        distance = (spot_price - strike) / spot_price
        time_factor = min(Decimal(dte) / Decimal("365"), Decimal("1"))
        call_delta = min(Decimal("0.95"), max(Decimal("0.05"), Decimal("0.50") + distance))
        delta = call_delta if contract_type == ContractType.CALL else call_delta - Decimal("1")
        gamma = Decimal("0.04") / (Decimal("1") + abs(distance) * Decimal("10"))
        gamma /= Decimal("1") + time_factor
        theta = -(Decimal("0.01") + (Decimal("1") - time_factor) * Decimal("0.01"))
        vega = Decimal("0.08") + time_factor * Decimal("0.12")
        direction = Decimal("1") if contract_type == ContractType.CALL else Decimal("-1")
        charm = -direction * distance / (Decimal("1") + Decimal(dte))
        vanna = direction * distance * (Decimal("1") - time_factor)
        return Greeks(
            delta=delta,
            gamma=gamma,
            theta=theta,
            vega=vega,
            charm=charm,
            vanna=vanna,
        )
