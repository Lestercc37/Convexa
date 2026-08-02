from __future__ import annotations

from decimal import Decimal

from backend.domain.entities import ContractType, MaxPain, MaxPainStrikePain, OptionChain
from backend.domain.ports import IMaxPainCalculator


class FakeMaxPainCalculator(IMaxPainCalculator):
    """Deterministic institutional Max Pain calculator."""

    def calculate(self, chain: OptionChain) -> MaxPain:
        strikes = tuple(sorted({contract.strike for contract in chain.contracts}))
        ranking: list[MaxPainStrikePain] = []

        for candidate_strike in strikes:
            total_call_pain = Decimal("0")
            total_put_pain = Decimal("0")
            for contract in chain.contracts:
                if contract.contract_type == ContractType.CALL:
                    intrinsic_pain = max(candidate_strike - contract.strike, Decimal("0"))
                    total_call_pain += intrinsic_pain * contract.open_interest
                else:
                    intrinsic_pain = max(contract.strike - candidate_strike, Decimal("0"))
                    total_put_pain += intrinsic_pain * contract.open_interest
            ranking.append(
                MaxPainStrikePain(
                    strike=candidate_strike,
                    total_call_pain=total_call_pain,
                    total_put_pain=total_put_pain,
                    total_pain=total_call_pain + total_put_pain,
                )
            )

        ranking = sorted(ranking, key=lambda item: (item.total_pain, item.strike))[:5]
        best = ranking[0] if ranking else None
        return MaxPain(
            symbol=chain.symbol,
            as_of=chain.as_of,
            max_pain_strike=best.strike if best else Decimal("0"),
            total_call_pain=best.total_call_pain if best else Decimal("0"),
            total_put_pain=best.total_put_pain if best else Decimal("0"),
            total_pain=best.total_pain if best else Decimal("0"),
            ranking=tuple(ranking),
        )
