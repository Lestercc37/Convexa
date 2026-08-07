from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from backend.domain.entities import (
    DailyGammaReference,
    FlowEvent,
    GammaAggregate,
    MarketPrice,
    OptionChain,
    Underlying,
    WhaleThreshold,
)
from backend.domain.underlyings import ACTIVE_UNDERLYINGS


class InMemoryStorage:
    def __init__(self) -> None:
        self._underlyings: dict[str, Underlying] = {
            underlying.symbol: underlying for underlying in ACTIVE_UNDERLYINGS
        }
        self._chains: dict[str, list[OptionChain]] = {}
        self._gamma: dict[str, list[GammaAggregate]] = {}
        self._prices: dict[str, MarketPrice] = {}
        self._price_history: dict[str, list[MarketPrice]] = {}
        self._flow: dict[str, list[FlowEvent]] = {}
        self._daily_gamma: dict[str, dict[date, DailyGammaReference]] = {}
        self._whale_thresholds: dict[str, WhaleThreshold] = {
            underlying.symbol: WhaleThreshold(
                symbol=underlying.symbol,
                unusual_min=Decimal("40000"),
                whale_min=Decimal("150000"),
                unusual_multiplier=Decimal("3.0"),
                whale_multiplier=Decimal("6.0"),
            )
            for underlying in ACTIVE_UNDERLYINGS
        }

    def list_underlyings(self) -> list[Underlying]:
        return sorted(self._underlyings.values(), key=lambda item: item.symbol)

    def save_whale_threshold(self, threshold: WhaleThreshold) -> None:
        self._whale_thresholds[threshold.symbol] = threshold

    def get_whale_thresholds(self) -> dict[str, WhaleThreshold]:
        return dict(self._whale_thresholds)

    def save_chain_snapshot(self, chain: OptionChain) -> None:
        self._chains.setdefault(chain.symbol, []).append(chain)

    def get_latest_chain_snapshot(
        self, underlying: str, expiration: date | None = None
    ) -> OptionChain | None:
        chains = self._chains.get(underlying.upper(), [])
        if expiration is not None:
            chains = [
                c
                for c in chains
                if any(contract.expiration == expiration for contract in c.contracts)
            ]
        return max(chains, key=lambda chain: chain.as_of, default=None)

    def save_gamma_aggregate(self, gamma: GammaAggregate) -> None:
        self._gamma.setdefault(gamma.symbol, []).append(gamma)

    def get_latest_gamma_aggregate(self, underlying: str) -> GammaAggregate | None:
        return max(
            self._gamma.get(underlying.upper(), []), key=lambda item: item.as_of, default=None
        )

    def get_gamma_history(
        self, underlying: str, start: datetime, end: datetime
    ) -> list[GammaAggregate]:
        return [
            item for item in self._gamma.get(underlying.upper(), []) if start <= item.as_of <= end
        ]

    def save_market_price(self, price: MarketPrice) -> None:
        self._prices[price.symbol] = price
        self._price_history.setdefault(price.symbol, []).append(price)

    def get_latest_price(self, underlying: str) -> MarketPrice | None:
        return self._prices.get(underlying.upper())

    def get_price_history(
        self, underlying: str, start: datetime, end: datetime
    ) -> list[MarketPrice]:
        history = self._price_history.get(underlying.upper(), [])
        return sorted(
            (price for price in history if start <= price.as_of <= end),
            key=lambda price: price.as_of,
        )

    def save_flow_event(self, event: FlowEvent) -> None:
        self._flow.setdefault(event.symbol, []).append(event)

    def get_flow_events(
        self, underlying: str, since: datetime | None = None, limit: int = 100
    ) -> list[FlowEvent]:
        events = self._flow.get(underlying.upper(), [])
        if since is not None:
            events = [event for event in events if event.as_of >= since]
        return sorted(events, key=lambda event: event.as_of, reverse=True)[:limit]

    def get_recent_flow(self, underlying: str, limit: int = 20) -> list[FlowEvent]:
        return self.get_flow_events(underlying, limit=limit)

    def save_daily_gamma_reference(self, reference: DailyGammaReference) -> None:
        references = self._daily_gamma.setdefault(reference.symbol, {})
        references[reference.date] = reference

    def get_daily_gamma_references(
        self, underlying: str, limit: int = 60
    ) -> list[DailyGammaReference]:
        references = self._daily_gamma.get(underlying.upper(), {}).values()
        return sorted(references, key=lambda item: item.date, reverse=True)[:limit]
