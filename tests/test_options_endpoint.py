from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from backend.adapters.providers.mock.provider import MockDataProvider
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
        # A date that has not expired yet (an expired one is never fetched live, see
        # test_chain_fallback_does_not_write.py).
        expiration = datetime.now(UTC).date() + timedelta(days=30)
        response = client.get(f"/api/v1/chain/qqq?expiration={expiration.isoformat()}")
        stored = app.state.container.storage.get_latest_chain_snapshot("QQQ")

    assert response.status_code == 200
    # The fallback answers from the provider but does NOT persist: the scheduler is
    # the only writer of option_chain_snapshots.
    assert stored is None
    assert {contract["occ_symbol"][3:9] for contract in response.json()["contracts"]} == {expiration.strftime("%y%m%d")}


def test_chain_expirations_get_returns_only_dates_not_full_contracts() -> None:
    # The option-chain-viewer/Volatility Smile dropdown only needs the
    # distinct expiration dates -- confirmed live, 2026-09-22: fetching
    # the full unscoped chain just to read off `.expiration` cost a
    # ~8,000-contract, 2.3MB response for SPX after the Gamma Flip
    # wide-search fix (PR #159), heavy enough to help starve
    # /chain/{symbol}'s shared threadpool into real 500s.
    with TestClient(app) as client:
        # get_option_chain_expirations is storage-only (never live-fetches,
        # see its own docstring) -- /chain/spy only supplies the expected dates
        # here; the storage seed right after it stands in for what a real
        # scheduler cycle already wrote by the time anyone opens the dropdown
        # (the /chain fallback no longer persists anything).
        full_chain = client.get("/api/v1/chain/spy")
        app.state.container.storage.save_chain_snapshot(MockDataProvider().get_option_chain("SPY"))
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
        stored = app.state.container.storage.get_latest_gamma_aggregate("SPY", view="tactical")

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


def test_gamma_get_serves_the_tactical_aggregate_and_no_structural_one_exists() -> None:
    """The refresh cycle persists only the Tactical (0-2 DTE) aggregate since 2026-10-16 (Structural
    retired): the default and ?view=tactical answer, ?view=structural has nothing to return. The
    mock fixture lists nothing within 0-2 DTE of its as_of, so Tactical falls back to the nearest
    listed expiration (TACTICAL_FALLBACK_WINDOW_DAYS) and still has data."""
    with TestClient(app) as client:
        trigger = client.post("/internal/trigger-calculation/spy")
        structural = client.get("/api/v1/gamma/spy?view=structural")
        tactical = client.get("/api/v1/gamma/spy?view=tactical")
        default = client.get("/api/v1/gamma/spy")

    assert trigger.status_code == 200
    assert structural.status_code == 404
    assert tactical.status_code == 200
    assert tactical.json()["view"] == "tactical"
    assert tactical.json()["has_data"] is True
    assert default.json() == tactical.json()


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
        stored = app.state.container.storage.get_latest_gamma_aggregate("SPY", view="tactical")

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
        stored = app.state.container.storage.get_latest_gamma_aggregate("SPY", view="tactical")

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


def test_async_chain_expirations_read_matches_the_sync_one_and_404s_when_nothing_is_stored() -> None:
    # The route reads the latest stored snapshot through the async storage (the sync read took 26-32 s for SPX/NDX with the market closed,
    # 2026-10-18); the dates must be the same ones the sync twin gives, and "nothing stored" must still be the uniform not-found.
    import asyncio

    from backend.domain.use_cases.errors import NotFoundError
    from backend.domain.use_cases import get_option_chain_expirations, get_option_chain_expirations_async

    with TestClient(app) as client:
        container = app.state.container
        try:
            asyncio.run(get_option_chain_expirations_async(container.async_market_storage, "SPY"))
        except NotFoundError:
            pass
        else:  # pragma: no cover
            raise AssertionError("expected NotFoundError before anything is stored")
        container.storage.save_chain_snapshot(MockDataProvider().get_option_chain("SPY"))
        sync_chain = get_option_chain_expirations(container.storage, "SPY")
        async_chain = asyncio.run(get_option_chain_expirations_async(container.async_market_storage, "SPY"))
        response = client.get("/api/v1/chain/spy/expirations")

    assert {c.expiration for c in async_chain.contracts} == {c.expiration for c in sync_chain.contracts}
    assert response.status_code == 200
    assert response.json()["expirations"] == sorted({str(c.expiration) for c in sync_chain.contracts})
