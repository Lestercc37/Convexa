from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from backend.adapters.providers.mock.provider import MockDataProvider
from backend.adapters.storage.memory import InMemoryStorage
from backend.domain.entities import (
    DailyGammaReference,
    GammaAggregate,
    MarketPrice,
    MarketSnapshot,
)
from backend.domain.use_cases.calculate_derived_metrics import (
    CalculateDerivedMetricsUseCase,
    calculate_iv_rank,
    capture_daily_gamma_reference,
    freshness_component,
    market_bias_label,
    percentile_rank,
    volatility_regime_label,
)


def test_derived_metrics_are_provisional_before_twenty_days() -> None:
    storage = _storage_with_current_state(freshness_seconds=0)
    _seed_references(storage, 19)

    metrics = CalculateDerivedMetricsUseCase(storage).execute("SPY")

    assert metrics.dealer_impact_score.value is None
    assert metrics.dealer_impact_score.provisional is True
    assert metrics.market_bias.score is None
    assert metrics.market_bias.label is None
    assert metrics.market_bias.provisional is True
    assert metrics.signal_alignment_score.value == Decimal("100")
    assert metrics.signal_alignment_score.provisional is True
    assert metrics.signal_alignment_score.days_accumulated == 19
    assert metrics.volatility_regime.iv_rank is None
    assert metrics.volatility_regime.label is None
    assert metrics.volatility_regime.provisional is True


def test_all_derived_formulas_match_known_twenty_day_case() -> None:
    storage = _storage_with_current_state(freshness_seconds=120)
    _seed_references(storage, 20)

    metrics = CalculateDerivedMetricsUseCase(storage).execute("SPY")

    assert metrics.dealer_impact_score.value == Decimal("100")
    assert metrics.dealer_impact_score.provisional is False
    assert metrics.signal_alignment_score.value == Decimal("92.500")
    assert metrics.signal_alignment_score.provisional is False
    assert metrics.market_bias.score == Decimal("95.00")
    assert metrics.market_bias.label == "bullish"
    assert metrics.market_bias.provisional is False
    assert metrics.market_bias.days_accumulated == 20
    assert metrics.volatility_regime.iv_rank == Decimal("50")
    assert metrics.volatility_regime.label == "moderate"
    assert metrics.volatility_regime.provisional is False
    assert metrics.volatility_regime.days_accumulated == 20


def test_percentile_freshness_and_market_threshold_boundaries() -> None:
    assert percentile_rank(
        Decimal("2"),
        [Decimal("1"), Decimal("2"), Decimal("2"), Decimal("3")],
    ) == Decimal("75")
    now = datetime(2026, 8, 2, 14, 35, tzinfo=timezone.utc)
    assert freshness_component(now, now - timedelta(seconds=60)) == Decimal("100")
    assert freshness_component(now, now - timedelta(seconds=180)) == Decimal("50")
    assert freshness_component(now, now - timedelta(seconds=301)) == Decimal("0")
    assert market_bias_label(Decimal("65")) == "neutral"
    assert market_bias_label(Decimal("65.01")) == "bullish"
    assert market_bias_label(Decimal("35")) == "neutral"
    assert market_bias_label(Decimal("34.99")) == "bearish"
    assert volatility_regime_label(Decimal("29.99")) == "low"
    assert volatility_regime_label(Decimal("30")) == "moderate"
    assert volatility_regime_label(Decimal("70")) == "moderate"
    assert volatility_regime_label(Decimal("70.01")) == "high"
    assert calculate_iv_rank(Decimal("0.25"), [Decimal("0.25"), Decimal("0.25")]) == Decimal("50")


def test_mock_provider_supplies_deterministic_atm_iv() -> None:
    first = MockDataProvider().get_underlying_snapshot("SPY")
    second = MockDataProvider().get_underlying_snapshot("SPY")

    assert first.atm_iv == Decimal("0.22")
    assert second.atm_iv == Decimal("0.22")


def test_daily_reference_is_captured_only_during_0935_eastern() -> None:
    storage = InMemoryStorage()
    gamma = _gamma(datetime(2026, 8, 3, 13, 35, tzinfo=timezone.utc))
    inside = MarketSnapshot(
        symbol="SPY",
        as_of=datetime(2026, 8, 3, 13, 35, 59, tzinfo=timezone.utc),
        price=Decimal("90"),
        volume=1000,
        pc_oi_ratio=Decimal("1.25"),
        skew_25d=Decimal("0.06"),
        atm_iv=Decimal("0.25"),
    )
    outside = MarketSnapshot(
        symbol="SPY",
        as_of=datetime(2026, 8, 3, 13, 36, tzinfo=timezone.utc),
        price=Decimal("90"),
        volume=1000,
        pc_oi_ratio=Decimal("1.30"),
        skew_25d=Decimal("0.07"),
        atm_iv=Decimal("0.30"),
    )

    assert capture_daily_gamma_reference(storage, gamma, inside) is True
    assert capture_daily_gamma_reference(storage, gamma, outside) is False
    assert storage.get_daily_gamma_references("SPY") == [
        DailyGammaReference(
            date=date(2026, 8, 3),
            symbol="SPY",
            net_gamma=Decimal("-20"),
            pc_oi_ratio=Decimal("1.25"),
            skew_25d=Decimal("0.06"),
            atm_iv=Decimal("0.25"),
        )
    ]


def _storage_with_current_state(freshness_seconds: int) -> InMemoryStorage:
    storage = InMemoryStorage()
    gamma_as_of = datetime(2026, 8, 3, 13, 35, tzinfo=timezone.utc)
    storage.save_gamma_aggregate(_gamma(gamma_as_of))
    storage.save_market_price(
        MarketPrice(
            symbol="SPY",
            as_of=gamma_as_of + timedelta(seconds=freshness_seconds),
            price=Decimal("90"),
            volume=1000,
        )
    )
    return storage


def _gamma(as_of: datetime) -> GammaAggregate:
    return GammaAggregate(
        symbol="SPY",
        as_of=as_of,
        gamma_flip=Decimal("100"),
        call_wall=Decimal("110"),
        put_wall=Decimal("90"),
        max_pain=Decimal("100"),
        net_gamma=Decimal("-20"),
        dealer_gamma_notional=Decimal("-2000"),
    )


def _seed_references(storage: InMemoryStorage, days: int) -> None:
    today = date(2026, 8, 3)
    for offset in range(days):
        rank_value = days - offset
        if offset == 0:
            net_gamma = Decimal("-20")
            pc_oi_ratio = Decimal("1")
            skew_25d = Decimal("1")
            atm_iv = Decimal("10")
        else:
            net_gamma = Decimal(rank_value)
            pc_oi_ratio = Decimal(rank_value + 1)
            skew_25d = Decimal(rank_value + 1)
            atm_iv = Decimal(offset)
        storage.save_daily_gamma_reference(
            DailyGammaReference(
                date=today - timedelta(days=offset),
                symbol="SPY",
                net_gamma=net_gamma,
                pc_oi_ratio=pc_oi_ratio,
                skew_25d=skew_25d,
                atm_iv=atm_iv,
            )
        )
