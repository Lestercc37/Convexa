from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi.testclient import TestClient

from backend.adapters.storage.memory import InMemoryStorage
from backend.domain.entities import GammaAggregate
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
        WhaleAlert("SPY", "SPY-C", WhaleAlertType.UNUSUAL, Decimal("40000"), AS_OF),
        WhaleAlert(
            "QQQ",
            "QQQ-C",
            WhaleAlertType.WHALE,
            Decimal("150000"),
            AS_OF + timedelta(minutes=1),
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
