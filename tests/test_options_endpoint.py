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


def test_chain_expirations_get_returns_only_dates_not_full_contracts() -> None:
    # The option-chain-viewer/Volatility Smile dropdown only needs the
    # distinct expiration dates -- confirmed live, 2026-09-22: fetching
    # the full unscoped chain just to read off `.expiration` cost a
    # ~8,000-contract, 2.3MB response for SPX after the Gamma Flip
    # wide-search fix (PR #159), heavy enough to help starve
    # /chain/{symbol}'s shared threadpool into real 500s.
    with TestClient(app) as client:
        # get_option_chain_expirations is storage-only (never live-fetches,
        # see its own docstring) -- /chain/spy runs first here purely to
        # seed storage the same way a real scheduler cycle already would
        # have by the time anyone opens the dropdown, not because the
        # expirations route itself needs it.
        full_chain = client.get("/api/v1/chain/spy")
        response = client.get("/api/v1/chain/spy/expirations")

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == 1
    assert payload["symbol"] == "SPY"
    assert "contracts" not in payload
    expected = sorted({contract["expiration"] for contract in full_chain.json()["contracts"]})
    assert payload["expirations"] == expected
    assert len(payload["expirations"]) > 0


def test_chain_expirations_get_returns_uniform_not_found_when_nothing_is_stored() -> None:
    # Storage-only, by design (see get_option_chain_expirations' own
    # docstring) -- must 404 like every other read-only route here
    # instead of silently triggering the live fetch it deliberately
    # avoids.
    with TestClient(app) as client:
        response = client.get("/api/v1/chain/spy/expirations")

    assert response.status_code == 404
    assert response.json() == {
        "schema_version": 1,
        "error": {
            "code": "NOT_FOUND",
            "message": "No option chain found for SPY",
        },
    }


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
        "delta_exposure",
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


def test_gamma_get_view_query_param_routes_to_the_tactical_aggregate() -> None:
    """MockDataProvider's fixture chain (as_of 2026-01-15, its only
    expiration 2026-02-20, 36 days out) lists nothing within 0-2 DTE of
    its own as_of, so Tactical falls back to that same nearest listed
    expiration (see TACTICAL_FALLBACK_WINDOW_DAYS' own comment) -- live
    end to end through the real trigger-calculation -> get pipeline.
    Both views land on the same single expiration here (the fixture only
    has one), so they read identically; test_gamma_views.py's own
    execute_both test covers the case where the two windows genuinely
    diverge."""
    with TestClient(app) as client:
        trigger = client.post("/internal/trigger-calculation/spy")
        structural = client.get("/api/v1/gamma/spy")
        tactical = client.get("/api/v1/gamma/spy?view=tactical")
        default = client.get("/api/v1/gamma/spy")

    assert trigger.status_code == 200
    assert structural.status_code == 200
    assert tactical.status_code == 200

    assert structural.json()["view"] == "structural"
    assert structural.json()["has_data"] is True

    assert tactical.json()["view"] == "tactical"
    assert tactical.json()["has_data"] is True
    # Both views land on the exact same (only) expiration this fixture
    # has, so every computed field matches structural's -- including
    # call_wall/put_wall being None on both: the fixture's symmetric
    # call+put open interest at every strike nets each strike's gamma to
    # zero, a real "no candidate found" outcome (see
    # test_tactical_walls_include_0dte_unlike_structural's own comment),
    # not something this fallback should manufacture a value for.
    assert tactical.json()["call_wall"] == structural.json()["call_wall"]
    assert tactical.json()["put_wall"] == structural.json()["put_wall"]

    # Omitting the param must still return exactly today's structural
    # numbers -- no regression for every consumer that predates this
    # feature and never passes `view`.
    assert default.json() == structural.json()


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


def test_gamma_profile_items_expose_the_real_open_interest_and_volume() -> None:
    # P7 fix (2026-09-21): these two fields used to always serialize as 0
    # (GammaAggregateItemResponse declared them, but the serializer never
    # read them off the item, and the calculator upstream never summed
    # them in the first place) -- MockDataProvider's own SPY fixture has
    # real, non-zero values, so a real end-to-end regression would show up
    # here as every item being 0 again.
    with TestClient(app) as client:
        client.post("/internal/trigger-calculation/spy")
        response = client.get("/api/v1/gamma/spy/profile")
        stored = app.state.container.storage.get_latest_gamma_aggregate("SPY")

    assert response.status_code == 200
    payload = response.json()
    assert stored is not None
    for item, stored_item in zip(payload["items"], stored.items, strict=True):
        assert item["open_interest"] == stored_item.open_interest
        assert item["volume"] == stored_item.volume
    assert any(item["open_interest"] > 0 for item in payload["items"])
    assert any(item["volume"] > 0 for item in payload["items"])


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
