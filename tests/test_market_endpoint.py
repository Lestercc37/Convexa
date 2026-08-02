from fastapi.testclient import TestClient

from backend.main import app


def test_market_endpoint_reads_persisted_snapshot() -> None:
    with TestClient(app) as client:
        missing = client.get("/api/v1/market/spy")
        client.post("/internal/trigger-calculation/spy")
        response = client.get("/api/v1/market/spy")

    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "NOT_FOUND"
    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == 1
    assert payload["symbol"] == "SPY"
    assert payload["price"] == 552.25


def test_openapi_documents_versioned_market_response_model() -> None:
    with TestClient(app) as client:
        response = client.get("/openapi.json")

    response_schema = response.json()["paths"]["/api/v1/market/{symbol}"]["get"][
        "responses"
    ]["200"]["content"]["application/json"]["schema"]
    assert response_schema["$ref"].endswith("/MarketSnapshotResponse")
