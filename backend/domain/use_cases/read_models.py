from __future__ import annotations

from datetime import date, datetime, timezone

from backend.domain.entities import MarketSnapshot, OptionChain
from backend.domain.ports import IDataProvider, IStorage
from backend.domain.use_cases.calculate_anchored_vwap import (
    calculate_anchored_vwap,
    calculate_session_open,
)
from backend.domain.use_cases.calculate_atr_range import REQUIRED_DAILY_BARS, calculate_atr_range
from backend.domain.use_cases.calculate_closing_dynamics import calculate_closing_dynamics
from backend.domain.use_cases.calculate_expected_move import (
    calculate_expected_move,
    calculate_time_to_close_pct,
)
from backend.domain.use_cases.errors import NotFoundError
from backend.domain.use_cases.market_hours import is_market_open

DEFAULT_FRESHNESS_SECONDS = 60


def get_option_chain(
    storage: IStorage,
    provider: IDataProvider,
    underlying: str,
    expiration: date | None = None,
    freshness_seconds: int = DEFAULT_FRESHNESS_SECONDS,
) -> OptionChain:
    chain = storage.get_latest_chain_snapshot(underlying, expiration)
    now = datetime.now(timezone.utc)
    if chain is not None and (
        (now - chain.as_of).total_seconds() <= freshness_seconds or not is_market_open(now)
    ):
        # Outside market hours the scheduler has already gone quiet (same
        # is_market_open gate as CalculateGammaExposureOrchestrator's own
        # storage-only reads) -- serve the stored snapshot no matter its
        # age instead of refreshing live, so this endpoint stops being the
        # one path that still hit the provider after close (confirmed
        # live, 2026-09: it was overwriting a good pre-close snapshot with
        # a degenerate all-zero-gamma one, see calculate_bsm_greeks).
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
    chain = storage.get_latest_chain_snapshot(underlying)
    if chain is None:
        raise NotFoundError(f"No option chain found for {underlying}")
    price_history = storage.get_price_history(
        underlying, calculate_session_open(price.as_of), price.as_of
    )
    daily_bars = storage.get_daily_bars(underlying, limit=REQUIRED_DAILY_BARS)
    time_to_close_pct = calculate_time_to_close_pct(price.as_of)
    return MarketSnapshot(
        symbol=price.symbol,
        as_of=price.as_of,
        price=price.price,
        volume=price.volume,
        gamma=gamma,
        expected_move=calculate_expected_move(chain, price.as_of),
        anchored_vwap=calculate_anchored_vwap(price_history, price.as_of),
        atr_range=calculate_atr_range(daily_bars, price_history),
        closing_dynamics=calculate_closing_dynamics(gamma, price.price, time_to_close_pct),
        recent_flow=tuple(storage.get_recent_flow(underlying)),
    )
