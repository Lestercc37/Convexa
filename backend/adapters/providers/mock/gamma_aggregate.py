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
        # P7 fix (2026-09-21): GammaAggregateItem already had these two
        # fields (see storage/serializers, which already read and persist
        # them) -- this calculator was simply the one place in the chain
        # that never summed GammaExposure's own per-contract values into
        # them, so every item silently carried 0 regardless of the real
        # data upstream. Summed across every contract at a strike (both
        # calls and puts), same as contract_count_by_strike above.
        open_interest_by_strike: dict[Decimal, int] = defaultdict(int)
        volume_by_strike: dict[Decimal, int] = defaultdict(int)

        for exposure in exposures:
            if exposure.contract_type == ContractType.CALL:
                call_gamma_by_strike[exposure.strike] += exposure.dealer_gamma_exposure
            else:
                put_gamma_by_strike[exposure.strike] += exposure.dealer_gamma_exposure
            contract_count_by_strike[exposure.strike] += 1
            open_interest_by_strike[exposure.strike] += exposure.open_interest
            volume_by_strike[exposure.strike] += exposure.volume

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
                    open_interest=open_interest_by_strike[strike],
                    volume=volume_by_strike[strike],
                )
            )

        total_market_gamma = sum((item.net_gamma for item in items), Decimal("0"))
        # Ranked by total_gamma_exposure (|call| + |put|, each leg summed
        # independently), NOT by absolute_gamma (|call + put|, netted
        # first) -- confirmed live, 2026-09-24: a strike with large,
        # roughly offsetting call and put gamma (heavy two-sided dealer
        # hedging activity -- exactly the kind of strike that acts as a
        # real pinning magnet) nets to near-zero under absolute_gamma,
        # making it invisible to this ranking even though it demands the
        # most real hedging liquidity of any strike in the book. This is
        # the same class of bug already fixed for Call Wall/Put Wall
        # (net-gamma-based selection, see FakeWallCalculator) -- Absolute
        # Gamma Strike needed the opposite correction: it's supposed to
        # measure total two-sided hedging demand regardless of
        # directional bias, not a netted, bias-sensitive quantity. Likely
        # explains part of the unresolved "Large Gamma Strike" ranking
        # mismatch against a real reference platform documented in this
        # project's own history (that platform's own winning strikes
        # consistently carried far more OI than Convexa's, which is
        # exactly the signature of comparing against the wrong metric).
        # item.absolute_gamma itself is untouched -- it's still the
        # right (net, signed-magnitude) quantity for the GEX-by-strike
        # histogram each item feeds, a genuinely different use case from
        # "which single strike is the dominant hedging magnet."
        absolute_gamma_item = max(items, key=lambda item: item.total_gamma_exposure, default=None)
        absolute_gamma_strike = (
            absolute_gamma_item.strike if absolute_gamma_item is not None else Decimal("0")
        )
        peak_gamma_value = (
            absolute_gamma_item.total_gamma_exposure
            if absolute_gamma_item is not None
            else Decimal("0")
        )
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
            absolute_gamma_strike=absolute_gamma_strike,
            peak_gamma_value=peak_gamma_value,
        )
