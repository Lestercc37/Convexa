from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from decimal import Decimal

from backend.domain.entities import (
    ContractType,
    GammaAggregate,
    GammaAggregateItem,
    GammaExposure,
)
from backend.domain.ports import IGammaAggregateCalculator


class FakeGammaAggregateCalculator(IGammaAggregateCalculator):
    """Deterministic Gamma Aggregate calculator based on Gamma Exposure."""

    def calculate(
        self, exposures: tuple[GammaExposure, ...], symbol: str, as_of: datetime
    ) -> GammaAggregate:
        call_gamma_by_strike: dict[Decimal, Decimal] = defaultdict(lambda: Decimal("0"))
        put_gamma_by_strike: dict[Decimal, Decimal] = defaultdict(lambda: Decimal("0"))
        contract_count_by_strike: dict[Decimal, int] = defaultdict(int)

        for exposure in exposures:
            if exposure.contract_type == ContractType.CALL:
                call_gamma_by_strike[exposure.strike] += exposure.dealer_gamma_exposure
            else:
                put_gamma_by_strike[exposure.strike] += exposure.dealer_gamma_exposure
            contract_count_by_strike[exposure.strike] += 1

        items: list[GammaAggregateItem] = []
        positive_gamma = Decimal("0")
        negative_gamma = Decimal("0")
        for strike in sorted(contract_count_by_strike):
            call_gamma_exposure = call_gamma_by_strike[strike]
            put_gamma_exposure = put_gamma_by_strike[strike]
            net_gamma = call_gamma_exposure + put_gamma_exposure
            absolute_gamma = abs(net_gamma)
            total_gamma_exposure = abs(call_gamma_exposure) + abs(put_gamma_exposure)
            if net_gamma > 0:
                positive_gamma += net_gamma
            elif net_gamma < 0:
                negative_gamma += net_gamma
            items.append(
                GammaAggregateItem(
                    strike=strike,
                    total_gamma_exposure=total_gamma_exposure,
                    call_gamma_exposure=call_gamma_exposure,
                    put_gamma_exposure=put_gamma_exposure,
                    net_gamma=net_gamma,
                    contract_count=contract_count_by_strike[strike],
                    absolute_gamma=absolute_gamma,
                )
            )

        total_market_gamma = sum((item.net_gamma for item in items), Decimal("0"))
        peak_gamma_item = max(items, key=lambda item: item.absolute_gamma, default=None)
        peak_gamma_strike = peak_gamma_item.strike if peak_gamma_item is not None else Decimal("0")
        peak_gamma_value = peak_gamma_item.absolute_gamma if peak_gamma_item is not None else Decimal("0")
        return GammaAggregate(
            symbol=symbol,
            as_of=as_of,
            items=tuple(items),
            total_market_gamma=total_market_gamma,
            positive_gamma=positive_gamma,
            negative_gamma=negative_gamma,
            total_gamma=total_market_gamma,
            net_gamma=total_market_gamma,
            dealer_gamma_notional=total_market_gamma,
            peak_gamma_strike=peak_gamma_strike,
            peak_gamma_value=peak_gamma_value,
        )
