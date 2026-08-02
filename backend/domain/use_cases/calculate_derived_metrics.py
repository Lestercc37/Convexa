from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from backend.domain.entities import (
    DailyGammaReference,
    DerivedMetrics,
    DerivedMetricValue,
    GammaAggregate,
    MarketBiasMetric,
    MarketSnapshot,
    VolatilityRegimeMetric,
)
from backend.domain.ports import IStorage
from backend.domain.use_cases.errors import NotFoundError

DEFAULT_HISTORY_DAYS = 60
MINIMUM_HISTORY_DAYS = 20
REFERENCE_HOUR_ET = 9
REFERENCE_MINUTE_ET = 35
EASTERN_TIME = ZoneInfo("America/New_York")


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
        gamma = self._storage.get_latest_gamma_aggregate(underlying)
        if gamma is None:
            raise NotFoundError(f"No gamma aggregate found for {underlying.upper()}")
        price = self._storage.get_latest_price(underlying)
        if price is None:
            raise NotFoundError(f"No market price found for {underlying.upper()}")

        references = self._storage.get_daily_gamma_references(underlying, self._history_days)
        days = len(references)
        has_history = days >= MINIMUM_HISTORY_DAYS

        dealer_impact = (
            percentile_rank(
                abs(references[0].net_gamma),
                [abs(reference.net_gamma) for reference in references],
            )
            if has_history
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
                provisional=not has_history,
                days_accumulated=days,
            ),
            signal_alignment_score=DerivedMetricValue(
                value=alignment,
                provisional=not has_history,
                days_accumulated=days,
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
    """Persist the daily sample only during 09:35:00-09:35:59 ET."""
    eastern_timestamp = market.as_of.astimezone(EASTERN_TIME)
    if (
        eastern_timestamp.hour != REFERENCE_HOUR_ET
        or eastern_timestamp.minute != REFERENCE_MINUTE_ET
    ):
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
