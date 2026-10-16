from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from backend.domain.entities import (
    DailyGammaReference,
    DerivedMetrics,
    DerivedMetricValue,
    GammaAggregate,
    MarketBiasMetric,
    MarketPrice,
    MarketSnapshot,
    VolatilityRegimeMetric,
)
from backend.domain.ports import IAsyncMarketReadStorage, IStorage
from backend.domain.use_cases.futures_proxy import proxy_symbol_for
from backend.domain.use_cases.errors import NotFoundError

DEFAULT_HISTORY_DAYS = 60
MINIMUM_HISTORY_DAYS = 20
REFERENCE_HOUR_ET = 9
REFERENCE_MINUTE_ET = 35
LATE_CAPTURE_UNTIL_ET = (10, 30)  # (hour, minute) ET: latest a catch-up sample may still be taken
EASTERN_TIME = ZoneInfo("America/New_York")
# net_gamma references are only comparable with the Tactical (0-2 DTE) aggregate from these days on.
# Older daily_gamma_reference.net_gamma rows were sampled from the Structural (30/45-day) aggregate,
# a different scale: Dealer Impact ranks only the rows on or after the symbol's date (the other three
# series -- put/call OI, skew, ATM IV -- come from the market snapshot, not the aggregate, and keep
# their whole history).
#  - SPX/SPY/QQQ: 2026-07-13, the first stage-2 session -- those rows were rebuilt from the stage-2
#    history files with the Tactical rule (docs/hm_rebuild_tactical_reference.py + hm_load_tactical_reference.py).
#  - every other symbol: 2026-09-26, the day after the Tactical view first existed (2026-09-25); rows up
#    to the deploy day were rewritten from the stored Tactical aggregate at 09:35 by the same loader.
# Lower a date only together with a matching rebuilt history, never otherwise.
TACTICAL_NET_GAMMA_REFERENCES_FROM_DEFAULT = date(2026, 9, 26)
TACTICAL_NET_GAMMA_REFERENCES_FROM_BY_SYMBOL: dict[str, date] = {
    "SPX": date(2026, 7, 13),
    "SPY": date(2026, 7, 13),
    "QQQ": date(2026, 7, 13),
}


class CalculateDerivedMetricsUseCase:
    """Calculate proprietary metrics as a read-only storage projection."""

    def __init__(
        self,
        storage: IStorage,
        history_days: int = DEFAULT_HISTORY_DAYS,
    ) -> None:
        if history_days < MINIMUM_HISTORY_DAYS:
            raise ValueError("history_days cannot be lower than 20")
        self._storage = storage
        self._history_days = history_days

    def execute(self, underlying: str) -> DerivedMetrics:
        # ES/NQ have no data of their own: the scores describe the index they are proxied by
        underlying = proxy_symbol_for(underlying) or underlying
        gamma = self._storage.get_latest_gamma_aggregate(underlying, view="tactical")
        if gamma is None:
            raise NotFoundError(f"No gamma aggregate found for {underlying.upper()}")
        price = self._storage.get_latest_price(underlying)
        if price is None:
            raise NotFoundError(f"No market price found for {underlying.upper()}")

        references = self._storage.get_daily_gamma_references(underlying, self._history_days)
        return _compute_derived_metrics(gamma, price, references)


async def calculate_derived_metrics_async(
    storage: IAsyncMarketReadStorage,
    underlying: str,
    history_days: int = DEFAULT_HISTORY_DAYS,
) -> DerivedMetrics:
    """Async twin of `CalculateDerivedMetricsUseCase.execute` -- same
    reads and the same pure `_compute_derived_metrics`, just awaited so
    `/gamma/{symbol}` can run on the event loop instead of the
    scheduler's shared threadpool (see AsyncPostgreSQLStorage's own
    docstring)."""
    underlying = proxy_symbol_for(underlying) or underlying  # ES/NQ: the proxy index's scores
    gamma = await storage.get_latest_gamma_aggregate(underlying, view="tactical")
    if gamma is None:
        raise NotFoundError(f"No gamma aggregate found for {underlying.upper()}")
    price = await storage.get_latest_price(underlying)
    if price is None:
        raise NotFoundError(f"No market price found for {underlying.upper()}")
    references = await storage.get_daily_gamma_references(underlying, history_days)
    return _compute_derived_metrics(gamma, price, references)


def _compute_derived_metrics(
    gamma: GammaAggregate,
    price: MarketPrice,
    references: list[DailyGammaReference],
) -> DerivedMetrics:
    days = len(references)
    has_history = days >= MINIMUM_HISTORY_DAYS
    # Dealer Impact (and the alignment score that uses it) rank net_gamma only against Tactical-scale rows
    net_gamma_from = TACTICAL_NET_GAMMA_REFERENCES_FROM_BY_SYMBOL.get(
        gamma.symbol.upper(), TACTICAL_NET_GAMMA_REFERENCES_FROM_DEFAULT
    )
    net_gamma_references = [reference for reference in references if reference.date >= net_gamma_from]
    net_gamma_days = len(net_gamma_references)
    has_net_gamma_history = net_gamma_days >= MINIMUM_HISTORY_DAYS

    dealer_impact = (
        percentile_rank(
            abs(net_gamma_references[0].net_gamma),
            [abs(reference.net_gamma) for reference in net_gamma_references],
        )
        if has_net_gamma_history
        else None
    )
    agreement = agreement_component(gamma, price.price)
    freshness = freshness_component(price.as_of, gamma.as_of)

    if dealer_impact is None:
        alignment = Decimal("0.60") * agreement + Decimal("0.40") * freshness
    else:
        extremity = abs(dealer_impact - Decimal("50")) * Decimal("2")
        alignment = (
            Decimal("0.40") * agreement
            + Decimal("0.30") * freshness
            + Decimal("0.30") * extremity
        )

    market_score: Decimal | None = None
    market_label: str | None = None
    iv_rank: Decimal | None = None
    volatility_label: str | None = None
    if has_history:
        pc_percentile = percentile_rank(
            references[0].pc_oi_ratio,
            [reference.pc_oi_ratio for reference in references],
        )
        skew_percentile = percentile_rank(
            references[0].skew_25d,
            [reference.skew_25d for reference in references],
        )
        market_score = Decimal("0.5") * (Decimal("100") - pc_percentile) + Decimal("0.5") * (
            Decimal("100") - skew_percentile
        )
        market_label = market_bias_label(market_score)
        iv_values = [reference.atm_iv for reference in references]
        iv_rank = calculate_iv_rank(references[0].atm_iv, iv_values)
        volatility_label = volatility_regime_label(iv_rank)

    return DerivedMetrics(
        dealer_impact_score=DerivedMetricValue(
            value=dealer_impact,
            provisional=not has_net_gamma_history,
            days_accumulated=net_gamma_days,
        ),
        signal_alignment_score=DerivedMetricValue(
            value=alignment,
            provisional=not has_net_gamma_history,
            days_accumulated=net_gamma_days,
        ),
        market_bias=MarketBiasMetric(
            score=market_score,
            label=market_label,
            provisional=not has_history,
            days_accumulated=days,
        ),
        volatility_regime=VolatilityRegimeMetric(
            iv_rank=iv_rank,
            label=volatility_label,
            provisional=not has_history,
            days_accumulated=days,
        ),
    )


def percentile_rank(current: Decimal, values: list[Decimal]) -> Decimal:
    """Return the inclusive percentile rank documented for Convexa metrics."""
    if not values:
        raise ValueError("percentile rank requires at least one value")
    observations_at_or_below = sum(value <= current for value in values)
    return Decimal(observations_at_or_below) / Decimal(len(values)) * Decimal("100")


def agreement_component(gamma: GammaAggregate, current_price: Decimal) -> Decimal:
    if gamma.gamma_flip is None:
        # No sign crossing found in the scanned strike range (see
        # GammaAggregate.gamma_flip's own comment) -- no independent
        # price-vs-flip signal to compare dealer_position against, so
        # this reports full agreement instead of inventing a mismatch.
        # Same "confirmed when there's nothing to disagree with"
        # convention as MarketSnapshot.dealer_mode_confirmed -- flagged
        # for review, not a fully settled design choice.
        return Decimal("100")
    gamma_mode = gamma.dealer_position
    price_mode = "long_gamma" if current_price >= gamma.gamma_flip else "short_gamma"
    return Decimal("100") if gamma_mode == price_mode else Decimal("40")


def freshness_component(as_of: datetime, gamma_as_of: datetime) -> Decimal:
    lag_seconds = max((as_of - gamma_as_of).total_seconds(), 0)
    if lag_seconds < 60:
        return Decimal("100")
    if lag_seconds > 300:
        return Decimal("0")
    return (Decimal("300") - Decimal(str(lag_seconds))) / Decimal("240") * Decimal("100")


def market_bias_label(score: Decimal) -> str:
    if score > 65:
        return "bullish"
    if score < 35:
        return "bearish"
    return "neutral"


def calculate_iv_rank(current: Decimal, values: list[Decimal]) -> Decimal:
    """Calculate IV Rank, using the documented midpoint for a flat window."""
    if not values:
        raise ValueError("IV Rank requires at least one value")
    minimum = min(values)
    maximum = max(values)
    if maximum == minimum:
        return Decimal("50")
    return (current - minimum) / (maximum - minimum) * Decimal("100")


def volatility_regime_label(iv_rank: Decimal) -> str:
    if iv_rank < 30:
        return "low"
    if iv_rank > 70:
        return "high"
    return "moderate"


def capture_daily_gamma_reference(
    storage: IStorage,
    gamma: GammaAggregate,
    market: MarketSnapshot,
) -> bool:
    """Persist the daily sample during 09:35:00-09:35:59 ET -- or, when no cycle landed in that minute (the scheduler's cycle is
    ~60-90 s, so many days have no row: SPX had none for 2026-10-13..16 and Dealer Impact then measures against a stale 'latest'
    row), at the FIRST cycle after it, up to LATE_CAPTURE_UNTIL_ET, if that day still has no row for the symbol."""
    eastern_timestamp = market.as_of.astimezone(EASTERN_TIME)
    in_the_minute = (
        eastern_timestamp.hour == REFERENCE_HOUR_ET and eastern_timestamp.minute == REFERENCE_MINUTE_ET
    )
    if not in_the_minute:
        after_the_minute = (eastern_timestamp.hour, eastern_timestamp.minute) > (
            REFERENCE_HOUR_ET,
            REFERENCE_MINUTE_ET,
        )
        before_cutoff = (eastern_timestamp.hour, eastern_timestamp.minute) <= LATE_CAPTURE_UNTIL_ET
        if not (after_the_minute and before_cutoff):
            return False
        latest = storage.get_daily_gamma_references(gamma.symbol, 1)
        if latest and latest[0].date == eastern_timestamp.date():
            return False
    storage.save_daily_gamma_reference(
        DailyGammaReference(
            date=eastern_timestamp.date(),
            symbol=gamma.symbol,
            net_gamma=gamma.net_gamma,
            pc_oi_ratio=market.pc_oi_ratio,
            skew_25d=market.skew_25d,
            atm_iv=market.atm_iv,
        )
    )
    return True
