from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from backend.adapters.storage.memory import InMemoryStorage
from backend.domain.entities import (
    ExposureLeadersSettings,
    GammaAggregate,
    InvalidOptionError,
    NegativeGammaBoardSettings,
)
from backend.domain.use_cases import (
    ScreenerPreset,
    WhaleAlert,
    WhaleAlertType,
    get_screener_preset,
)
from backend.domain.underlyings import ACTIVE_UNDERLYINGS
from backend.main import create_app

AS_OF = datetime(2026, 8, 6, 14, 30, tzinfo=timezone.utc)


def _aggregate(
    symbol: str,
    *,
    net_gamma: str,
    vanna: str,
    charm: str,
) -> GammaAggregate:
    return GammaAggregate(
        symbol=symbol,
        as_of=AS_OF,
        net_gamma=Decimal(net_gamma),
        gamma_flip=Decimal("100"),
        call_wall=Decimal("110"),
        put_wall=Decimal("90"),
        max_pain=Decimal("105"),
        vanna_exposure=Decimal(vanna),
        charm_exposure=Decimal(charm),
    )


def _storage() -> InMemoryStorage:
    storage = InMemoryStorage()
    storage.save_gamma_aggregate(
        _aggregate("SPY", net_gamma="-20", vanna="-200", charm="30")
    )
    storage.save_gamma_aggregate(
        _aggregate("QQQ", net_gamma="50", vanna="100", charm="-300")
    )
    storage.save_gamma_aggregate(
        _aggregate("SPX", net_gamma="-80", vanna="50", charm="100")
    )
    return storage


def test_unusual_activity_is_sorted_most_recent_first() -> None:
    alerts = (
        WhaleAlert(
            symbol="SPY",
            occ_symbol="SPY-C",
            alert_type=WhaleAlertType.UNUSUAL,
            amount=Decimal("40000"),
            as_of=AS_OF,
            estimated_buy_volume=Decimal("20000"),
            estimated_sell_volume=Decimal("20000"),
        ),
        WhaleAlert(
            symbol="QQQ",
            occ_symbol="QQQ-C",
            alert_type=WhaleAlertType.WHALE,
            amount=Decimal("150000"),
            as_of=AS_OF + timedelta(minutes=1),
            estimated_buy_volume=Decimal("100000"),
            estimated_sell_volume=Decimal("50000"),
        ),
    )

    results = get_screener_preset(
        _storage(), ScreenerPreset.UNUSUAL_OPTIONS_ACTIVITY, alerts
    )

    assert [item.symbol for item in results] == ["QQQ", "SPY"]
    assert results[0].alert_type is WhaleAlertType.WHALE


def test_negative_gamma_filters_and_sorts_by_absolute_value() -> None:
    results = get_screener_preset(_storage(), ScreenerPreset.NEGATIVE_GAMMA_BOARD)

    assert [(item.symbol, item.net_gamma) for item in results] == [
        ("SPX", Decimal("-80")),
        ("SPY", Decimal("-20")),
    ]


def test_max_pain_key_levels_returns_all_persisted_aggregates() -> None:
    storage = _storage()
    for underlying in ACTIVE_UNDERLYINGS:
        if underlying.symbol not in {"SPY", "QQQ", "SPX"}:
            storage.save_gamma_aggregate(
                _aggregate(
                    underlying.symbol,
                    net_gamma="0",
                    vanna="0",
                    charm="0",
                )
            )

    results = get_screener_preset(storage, ScreenerPreset.MAX_PAIN_KEY_LEVELS)

    assert [item.symbol for item in results] == sorted(
        underlying.symbol for underlying in ACTIVE_UNDERLYINGS
    )
    assert all(item.gamma_flip == Decimal("100") for item in results)
    assert all(item.max_pain == Decimal("105") for item in results)


def test_vanna_leaders_sort_by_absolute_exposure() -> None:
    results = get_screener_preset(_storage(), ScreenerPreset.VANNA_EXPOSURE_LEADERS)

    assert [(item.symbol, item.vanna_exposure) for item in results] == [
        ("SPY", Decimal("-200")),
        ("QQQ", Decimal("100")),
        ("SPX", Decimal("50")),
    ]


def test_charm_pressure_sorts_by_absolute_exposure() -> None:
    results = get_screener_preset(_storage(), ScreenerPreset.CHARM_DECAY_PRESSURE)

    assert [(item.symbol, item.charm_exposure) for item in results] == [
        ("QQQ", Decimal("-300")),
        ("SPX", Decimal("100")),
        ("SPY", Decimal("30")),
    ]


def test_memory_storage_seeds_only_the_3_configurable_presets() -> None:
    storage = InMemoryStorage()

    assert storage.get_screener_preset_settings(
        ScreenerPreset.NEGATIVE_GAMMA_BOARD
    ) == NegativeGammaBoardSettings()
    assert storage.get_screener_preset_settings(
        ScreenerPreset.VANNA_EXPOSURE_LEADERS
    ) == ExposureLeadersSettings()
    assert storage.get_screener_preset_settings(
        ScreenerPreset.CHARM_DECAY_PRESSURE
    ) == ExposureLeadersSettings()
    assert storage.get_screener_preset_settings(
        ScreenerPreset.UNUSUAL_OPTIONS_ACTIVITY
    ) is None
    assert storage.get_screener_preset_settings(ScreenerPreset.MAX_PAIN_KEY_LEVELS) is None


def test_negative_gamma_board_settings_reject_non_finite_net_gamma_max() -> None:
    with pytest.raises(InvalidOptionError):
        NegativeGammaBoardSettings(net_gamma_max=Decimal("NaN"))


def test_exposure_leaders_settings_reject_negative_min_magnitude() -> None:
    with pytest.raises(InvalidOptionError):
        ExposureLeadersSettings(min_magnitude=Decimal("-1"))


def test_exposure_leaders_settings_reject_non_positive_limit() -> None:
    with pytest.raises(InvalidOptionError):
        ExposureLeadersSettings(limit=0)


def test_negative_gamma_board_uses_configured_threshold() -> None:
    storage = _storage()
    storage.save_screener_preset_settings(
        ScreenerPreset.NEGATIVE_GAMMA_BOARD,
        NegativeGammaBoardSettings(net_gamma_max=Decimal("-50")),
    )

    results = get_screener_preset(storage, ScreenerPreset.NEGATIVE_GAMMA_BOARD)

    assert [item.symbol for item in results] == ["SPX"]


class _UnconfiguredStorage(InMemoryStorage):
    """Simulates a preset with no persisted settings row (e.g. a fresh
    PostgreSQL deployment before the seed migration ran)."""

    def get_screener_preset_settings(self, preset: ScreenerPreset) -> None:
        return None


def test_negative_gamma_board_falls_back_to_default_when_unconfigured() -> None:
    storage = _UnconfiguredStorage()
    storage.save_gamma_aggregate(_aggregate("SPY", net_gamma="-20", vanna="0", charm="0"))

    results = get_screener_preset(storage, ScreenerPreset.NEGATIVE_GAMMA_BOARD)

    assert [item.symbol for item in results] == ["SPY"]


def test_vanna_leaders_applies_configured_minimum_magnitude() -> None:
    storage = _storage()
    storage.save_screener_preset_settings(
        ScreenerPreset.VANNA_EXPOSURE_LEADERS,
        ExposureLeadersSettings(min_magnitude=Decimal("100")),
    )

    results = get_screener_preset(storage, ScreenerPreset.VANNA_EXPOSURE_LEADERS)

    assert [item.symbol for item in results] == ["SPY", "QQQ"]


def test_vanna_leaders_applies_configured_limit() -> None:
    storage = _storage()
    storage.save_screener_preset_settings(
        ScreenerPreset.VANNA_EXPOSURE_LEADERS,
        ExposureLeadersSettings(limit=1),
    )

    results = get_screener_preset(storage, ScreenerPreset.VANNA_EXPOSURE_LEADERS)

    assert [item.symbol for item in results] == ["SPY"]


def test_charm_pressure_applies_configured_minimum_and_limit_together() -> None:
    storage = _storage()
    storage.save_screener_preset_settings(
        ScreenerPreset.CHARM_DECAY_PRESSURE,
        ExposureLeadersSettings(min_magnitude=Decimal("50"), limit=1),
    )

    results = get_screener_preset(storage, ScreenerPreset.CHARM_DECAY_PRESSURE)

    assert [item.symbol for item in results] == ["QQQ"]


def test_screener_endpoint_is_read_only() -> None:
    app = create_app()
    aggregate = _aggregate("SPX", net_gamma="-80", vanna="50", charm="100")

    with TestClient(app) as client:
        app.state.container.storage.save_gamma_aggregate(aggregate)
        response = client.get("/api/v1/screener-presets/negative-gamma-board")
        alias_response = client.get("/api/v1/screener-presets/negative_gamma_board")
        missing = client.get("/api/v1/screener-presets/not-a-preset")

    assert response.status_code == 200
    assert response.json()["results"][0]["symbol"] == "SPX"
    assert alias_response.json() == response.json()
    assert missing.status_code == 404
