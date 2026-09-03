from fastapi.testclient import TestClient

from backend.main import app


def test_chain_get_uses_versioned_path_and_returns_all_greeks() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/chain/spy")

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == 1
    assert payload["symbol"] == "SPY"
    assert payload["spot_price"] == 552.25
    contract = payload["contracts"][0]
    assert {"delta", "gamma", "theta", "vega", "charm", "vanna"} <= contract.keys()


def test_chain_get_keeps_documented_provider_fallback_and_expiration() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/chain/qqq?expiration=2026-03-20")
        stored = app.state.container.storage.get_latest_chain_snapshot("QQQ")

    assert response.status_code == 200
    assert stored is not None
    assert {contract["occ_symbol"][3:9] for contract in response.json()["contracts"]} == {"260320"}


def test_gamma_get_is_read_only_and_returns_uniform_not_found() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/gamma/spy")

    assert response.status_code == 404
    assert response.json() == {
        "schema_version": 1,
        "error": {
            "code": "NOT_FOUND",
            "message": "No gamma aggregate found for SPY",
        },
    }


def test_internal_trigger_persists_consolidated_gamma_for_public_get() -> None:
    with TestClient(app) as client:
        trigger = client.post("/internal/trigger-calculation/spy")
        response = client.get("/api/v1/gamma/spy")
        stored = app.state.container.storage.get_latest_gamma_aggregate("SPY")

    assert trigger.status_code == 200
    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == 1
    assert payload["symbol"] == "SPY"
    assert {
        "gamma_flip",
        "call_wall",
        "put_wall",
        "max_pain",
        "net_gamma",
        "vega_exposure",
        "theta_exposure",
        "charm_exposure",
        "vanna_exposure",
    } <= payload.keys()
    assert payload["dealer_position"] in {"long_gamma", "short_gamma"}
    assert stored is not None
    assert payload["absolute_gamma_strike"] == float(stored.absolute_gamma_strike)
    assert payload["derived_metrics"] == {
        "dealer_impact_score": {
            "value": None,
            "provisional": True,
            "days_accumulated": 0,
        },
        "signal_alignment_score": {
            "value": 60,
            "provisional": True,
            "days_accumulated": 0,
        },
        "market_bias": {
            "score": None,
            "label": None,
            "provisional": True,
            "days_accumulated": 0,
        },
        "volatility_regime": {
            "iv_rank": None,
            "label": None,
            "provisional": True,
            "days_accumulated": 0,
        },
    }


def test_gamma_profile_get_is_read_only_and_returns_uniform_not_found() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/gamma/spy/profile")

    assert response.status_code == 404
    assert response.json() == {
        "schema_version": 1,
        "error": {
            "code": "NOT_FOUND",
            "message": "No gamma aggregate found for SPY",
        },
    }


def test_gamma_profile_returns_frozen_snapshot_with_per_strike_items() -> None:
    with TestClient(app) as client:
        trigger = client.post("/internal/trigger-calculation/spy")
        response = client.get("/api/v1/gamma/spy/profile")
        stored = app.state.container.storage.get_latest_gamma_aggregate("SPY")

    assert trigger.status_code == 200
    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == 1
    assert payload["symbol"] == "SPY"
    assert stored is not None
    # None (no sign crossing found -- see GammaAggregate.gamma_flip's
    # own comment) is a real, valid outcome here, not something to
    # coerce into a float.
    expected_gamma_flip = float(stored.gamma_flip) if stored.gamma_flip is not None else None
    assert payload["gamma_flip"] == expected_gamma_flip
    assert payload["max_pain"] == float(stored.max_pain)
    assert len(payload["items"]) == len(stored.items)
    assert len(payload["items"]) > 0
    assert {"strike", "net_gamma", "total_gamma_exposure"} <= payload["items"][0].keys()


def test_underlyings_history_and_flow_are_storage_backed_gets() -> None:
    with TestClient(app) as client:
        underlyings = client.get("/api/v1/underlyings")
        client.post("/internal/trigger-calculation/spy")
        history = client.get("/api/v1/gamma/spy/history")
        flow = client.get("/api/v1/flow/spy")

    assert underlyings.status_code == 200
    assert {item["symbol"] for item in underlyings.json()["underlyings"]} >= {
        "SPY",
        "QQQ",
        "SPX",
    }
    assert len(history.json()["items"]) == 1
    assert flow.json() == {"schema_version": 1, "symbol": "SPY", "events": []}


def test_public_calculation_posts_are_removed_and_absent_from_openapi() -> None:
    legacy_paths = (
        "/options/greeks",
        "/options/gamma-exposure",
        "/options/gamma-aggregate",
        "/options/gamma-flip",
        "/options/walls",
        "/options/max-pain",
    )
    with TestClient(app) as client:
        statuses = [client.post(path).status_code for path in legacy_paths]
        paths = client.get("/openapi.json").json()["paths"]

    assert statuses == [404] * len(legacy_paths)
    assert all(path not in paths for path in legacy_paths)
    assert "/internal/trigger-calculation/{symbol}" not in paths
    assert "/api/v1/chain/{symbol}" in paths
    assert "/api/v1/gamma/{symbol}" in paths


def test_health_endpoints_remain_unversioned() -> None:
    with TestClient(app) as client:
        health = client.get("/health")
        versioned_health = client.get("/api/v1/health")

    assert health.status_code == 200
    assert versioned_health.status_code == 404
