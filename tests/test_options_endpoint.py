from fastapi.testclient import TestClient

from backend.main import app


def test_options_endpoint_returns_option_chain() -> None:
    with TestClient(app) as client:
        response = client.get("/options/spy")

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == 1
    assert payload["symbol"] == "SPY"
    assert payload["as_of"] == "2026-01-15T14:30:00Z"
    assert len(payload["contracts"]) == 6
    assert payload["contracts"][0] == {
        "occ_symbol": "SPY260220C00540000",
        "strike": 540,
        "type": "call",
        "bid": 1.2,
        "ask": 1.25,
        "iv": 0.18,
        "delta": 0.42,
        "gamma": 0.03,
        "open_interest": 8000,
        "volume": 3400,
    }


def test_options_endpoint_accepts_expiration_query() -> None:
    with TestClient(app) as client:
        response = client.get("/options/qqq?expiration=2026-03-20")

    assert response.status_code == 200
    payload = response.json()
    assert payload["symbol"] == "QQQ"
    assert {contract["occ_symbol"][3:9] for contract in payload["contracts"]} == {"260320"}


def test_greeks_endpoint_rejects_missing_option_chain_fields() -> None:
    with TestClient(app) as client:
        response = client.post("/options/greeks", json={"additionalProp1": {}})

    assert response.status_code == 422
    assert response.status_code != 500


def test_gamma_exposure_endpoint_rejects_missing_option_chain_fields() -> None:
    with TestClient(app) as client:
        response = client.post("/options/gamma-exposure", json={"additionalProp1": {}})

    assert response.status_code == 422
    assert response.status_code != 500


def test_gamma_aggregate_endpoint_rejects_missing_option_chain_fields() -> None:
    with TestClient(app) as client:
        response = client.post("/options/gamma-aggregate", json={"additionalProp1": {}})

    assert response.status_code == 422
    assert response.status_code != 500


def test_gamma_flip_endpoint_rejects_missing_gamma_aggregate_fields() -> None:
    with TestClient(app) as client:
        response = client.post("/options/gamma-flip", json={"additionalProp1": {}})

    assert response.status_code == 422
    assert response.status_code != 500


def test_option_chain_request_schema_includes_valid_swagger_example() -> None:
    with TestClient(app) as client:
        response = client.get("/openapi.json")

    assert response.status_code == 200
    schema = response.json()["components"]["schemas"]["OptionChainRequest"]
    assert schema["example"]["symbol"] == "SPY"
    assert schema["example"]["contracts"][0]["occ_symbol"] == "SPY260220C00540000"


def test_options_post_request_bodies_use_option_chain_request_schema() -> None:
    with TestClient(app) as client:
        response = client.get("/openapi.json")

    assert response.status_code == 200
    paths = response.json()["paths"]
    for path in (
        "/options/greeks",
        "/options/gamma-exposure",
        "/options/gamma-aggregate",
        "/options/max-pain",
    ):
        request_body = paths[path]["post"]["requestBody"]["content"]["application/json"]
        assert request_body["schema"]["$ref"].endswith("/OptionChainRequest")
        assert request_body["examples"]["option_chain"]["value"]["symbol"] == "SPY"
        assert "additionalProp1" not in str(request_body)
    gamma_flip_body = paths["/options/gamma-flip"]["post"]["requestBody"]["content"][
        "application/json"
    ]
    assert gamma_flip_body["schema"]["$ref"].endswith("/GammaFlipRequest")
    assert gamma_flip_body["examples"]["gamma_aggregate"]["value"]["symbol"] == "SPY"
    assert "additionalProp1" not in str(gamma_flip_body)


def test_greeks_endpoint_rejects_domain_invalid_payload_with_422() -> None:
    payload = {
        "symbol": "SPY",
        "as_of": "2026-01-15T14:30:00Z",
        "contracts": [
            {
                "occ_symbol": "SPY260220C00540000",
                "strike": 540,
                "expiration": "2026-02-20",
                "type": "call",
                "bid": 1.25,
                "ask": 1.2,
                "iv": 0.18,
                "open_interest": 8000,
                "volume": 3400,
            }
        ],
    }

    with TestClient(app) as client:
        response = client.post("/options/greeks", json=payload)

    assert response.status_code == 422
    assert response.status_code != 500


def test_openapi_documents_option_response_models() -> None:
    with TestClient(app) as client:
        response = client.get("/openapi.json")

    assert response.status_code == 200
    openapi = response.json()
    paths = openapi["paths"]

    expected_refs = {
        ("/options/{symbol}", "get"): "OptionChainResponse",
        ("/options/greeks", "post"): "GreeksResponse",
        ("/options/gamma-exposure", "post"): "GammaExposureResponse",
        ("/options/gamma-aggregate", "post"): "GammaAggregateResponse",
        ("/options/gamma-flip", "post"): "GammaFlipResponse",
        ("/options/max-pain", "post"): "MaxPainResponse",
    }
    for (path, method), schema_name in expected_refs.items():
        response_schema = paths[path][method]["responses"]["200"]["content"]["application/json"][
            "schema"
        ]
        assert response_schema["$ref"].endswith(f"/{schema_name}")
        assert "additionalProp1" not in str(response_schema)

    schemas = openapi["components"]["schemas"]
    assert schemas["OptionChainResponse"]["properties"]["contracts"]["items"]["$ref"].endswith(
        "/OptionContractResponse"
    )
    assert schemas["GreeksResponse"]["properties"]["contracts"]["items"]["$ref"].endswith(
        "/GreeksContractResponse"
    )
    assert schemas["GammaExposureResponse"]["properties"]["items"]["items"]["$ref"].endswith(
        "/GammaExposureItemResponse"
    )
    assert schemas["GammaAggregateResponse"]["properties"]["items"]["items"]["$ref"].endswith(
        "/GammaAggregateItemResponse"
    )
    gamma_flip_types = {
        item["type"]
        for item in schemas["GammaFlipResponse"]["properties"]["gamma_flip_price"]["anyOf"]
    }
    assert "null" in gamma_flip_types
