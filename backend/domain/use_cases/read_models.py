from __future__ import annotations

from datetime import date, datetime, timezone

from backend.domain.entities import MarketSnapshot, OptionChain
from backend.domain.ports import IDataProvider, IStorage
from backend.domain.use_cases.errors import NotFoundError

DEFAULT_FRESHNESS_SECONDS = 60


def get_option_chain(storage: IStorage, provider: IDataProvider, underlying: str, expiration: date | None = None, freshness_seconds: int = DEFAULT_FRESHNESS_SECONDS) -> OptionChain:
    chain = storage.get_latest_chain_snapshot(underlying, expiration)
    if chain is not None and (datetime.now(timezone.utc) - chain.as_of).total_seconds() <= freshness_seconds:
        return chain
    chain = provider.get_option_chain(underlying, expiration)
    storage.save_chain_snapshot(chain)
    return chain


def get_flow(storage: IStorage, underlying: str, since: datetime | None = None, limit: int = 100):
    return storage.get_flow_events(underlying, since, limit)


def build_market_snapshot(storage: IStorage, underlying: str) -> MarketSnapshot:
    price = storage.get_latest_price(underlying)
    if price is None:
        raise NotFoundError(f"No market price found for {underlying}")
    gamma = storage.get_latest_gamma_aggregate(underlying)
    if gamma is None:
        raise NotFoundError(f"No gamma aggregate found for {underlying}")
    return MarketSnapshot(
        symbol=price.symbol,
        as_of=price.as_of,
        price=price.price,
        volume=price.volume,
        gamma=gamma,
        recent_flow=tuple(storage.get_recent_flow(underlying)),
    )
