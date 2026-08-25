from __future__ import annotations

from fastapi.testclient import TestClient

from backend.main import app


def test_list_endpoint_returns_the_3_configurable_presets_with_defaults() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/screener-preset-settings")

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == 1
    presets = {item["preset"] for item in payload["settings"]}
    assert presets == {
        "negative-gamma-board",
        "vanna-exposure-leaders",
        "charm-decay-pressure",
    }
    gamma = next(
        item for item in payload["settings"] if item["preset"] == "negative-gamma-board"
    )
    assert gamma == {
        "preset": "negative-gamma-board",
        "net_gamma_max": 0,
        "min_magnitude": None,
        "limit": None,
    }


def test_patch_negative_gamma_board_updates_the_persisted_row() -> None:
    with TestClient(app) as client:
        response = client.patch(
            "/api/v1/screener-preset-settings/negative-gamma-board",
            json={"net_gamma_max": -50},
        )
        assert response.status_code == 200
        assert response.json() == {
            "preset": "negative-gamma-board",
            "net_gamma_max": -50,
            "min_magnitude": None,
            "limit": None,
        }

        listed = client.get("/api/v1/screener-preset-settings").json()

    updated = next(
        item for item in listed["settings"] if item["preset"] == "negative-gamma-board"
    )
    assert updated["net_gamma_max"] == -50


def test_patch_negative_gamma_board_rejects_extra_fields() -> None:
    with TestClient(app) as client:
        response = client.patch(
            "/api/v1/screener-preset-settings/negative-gamma-board",
            json={"net_gamma_max": -50, "limit": 5},
        )

    assert response.status_code == 422


def test_patch_negative_gamma_board_rejects_missing_field() -> None:
    with TestClient(app) as client:
        response = client.patch(
            "/api/v1/screener-preset-settings/negative-gamma-board",
            json={},
        )

    assert response.status_code == 422


def test_patch_vanna_leaders_accepts_both_fields_as_null() -> None:
    with TestClient(app) as client:
        response = client.patch(
            "/api/v1/screener-preset-settings/vanna-exposure-leaders",
            json={"min_magnitude": None, "limit": None},
        )

    assert response.status_code == 200
    assert response.json() == {
        "preset": "vanna-exposure-leaders",
        "net_gamma_max": None,
        "min_magnitude": None,
        "limit": None,
    }


def test_patch_charm_decay_updates_both_fields() -> None:
    with TestClient(app) as client:
        response = client.patch(
            "/api/v1/screener-preset-settings/charm-decay-pressure",
            json={"min_magnitude": 1500, "limit": 10},
        )

    assert response.status_code == 200
    assert response.json() == {
        "preset": "charm-decay-pressure",
        "net_gamma_max": None,
        "min_magnitude": 1500,
        "limit": 10,
    }


def test_patch_exposure_leaders_rejects_partial_fields() -> None:
    with TestClient(app) as client:
        missing_limit = client.patch(
            "/api/v1/screener-preset-settings/vanna-exposure-leaders",
            json={"min_magnitude": 100},
        )
        extra_field = client.patch(
            "/api/v1/screener-preset-settings/vanna-exposure-leaders",
            json={"min_magnitude": 100, "limit": 5, "net_gamma_max": 0},
        )

    assert missing_limit.status_code == 422
    assert extra_field.status_code == 422


def test_patch_rejects_negative_min_magnitude_and_non_positive_limit() -> None:
    with TestClient(app) as client:
        negative = client.patch(
            "/api/v1/screener-preset-settings/vanna-exposure-leaders",
            json={"min_magnitude": -1, "limit": 5},
        )
        zero_limit = client.patch(
            "/api/v1/screener-preset-settings/vanna-exposure-leaders",
            json={"min_magnitude": 100, "limit": 0},
        )

    assert negative.status_code == 422
    assert zero_limit.status_code == 422


def test_patch_endpoint_returns_not_found_for_unknown_preset() -> None:
    with TestClient(app) as client:
        response = client.patch(
            "/api/v1/screener-preset-settings/not-a-preset",
            json={"net_gamma_max": 0},
        )

    assert response.status_code == 404


def test_patch_endpoint_returns_not_found_for_non_configurable_presets() -> None:
    with TestClient(app) as client:
        unusual = client.patch(
            "/api/v1/screener-preset-settings/unusual-options-activity",
            json={"net_gamma_max": 0},
        )
        max_pain = client.patch(
            "/api/v1/screener-preset-settings/max-pain-key-levels",
            json={"net_gamma_max": 0},
        )

    assert unusual.status_code == 404
    assert max_pain.status_code == 404


def test_patch_takes_effect_on_the_next_screener_request() -> None:
    with TestClient(app) as client:
        patch = client.patch(
            "/api/v1/screener-preset-settings/vanna-exposure-leaders",
            json={"min_magnitude": None, "limit": 1},
        )
        assert patch.status_code == 200

        response = client.get("/api/v1/screener-presets/vanna-exposure-leaders")

    assert response.status_code == 200
    assert len(response.json()["results"]) <= 1
