"""GET /market/{symbol}/candles: regular-session OHLC candles of the last N trading dates, aggregated in SQL (the SQL itself is exercised against
a real Postgres in test_postgresql_integration.py). Here: validation, the serializer, the ES/NQ refusal and the 30 s cache."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

from fastapi.testclient import TestClient

from backend.api.serializers import price_candles_response
from backend.domain.entities import PriceCandle
from backend.domain.use_cases import read_models
from backend.domain.use_cases.errors import NotFoundError
from backend.main import app

T0 = datetime(2026, 10, 16, 13, 30, tzinfo=UTC)  # 09:30 ET (EDT)


def test_serializer_sends_unix_start_seconds_and_plain_numbers() -> None:
    candles = [PriceCandle(time=T0, open=Decimal("7745.5"), high=Decimal("7750"), low=Decimal("7741"), close=Decimal("7748.25"))]

    payload = price_candles_response("spx", "5m", 5, candles)

    assert payload["symbol"] == "SPX"
    assert (payload["timeframe"], payload["sessions"]) == ("5m", 5)
    assert payload["candles"] == [{"time": int(T0.timestamp()), "open": 7745.5, "high": 7750, "low": 7741, "close": 7748.25}]


def test_endpoint_answers_an_empty_list_when_nothing_is_stored() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/market/SPX/candles")

    assert response.status_code == 200
    assert response.json() == {"schema_version": 1, "symbol": "SPX", "timeframe": "5m", "sessions": 5, "candles": []}


def test_endpoint_rejects_unknown_timeframes_and_session_counts() -> None:
    with TestClient(app) as client:
        for query in ("?timeframe=1m", "?timeframe=2h", "?sessions=0", "?sessions=11"):
            assert client.get(f"/api/v1/market/SPX/candles{query}").status_code == 422, query


def test_futures_have_no_multi_session_candles() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/market/ES/candles")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


class _CountingStorage:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int, int]] = []

    async def get_price_candles(self, underlying, bucket_seconds, sessions, since):
        self.calls.append((underlying, bucket_seconds, sessions))
        return [PriceCandle(time=T0, open=Decimal(1), high=Decimal(2), low=Decimal(1), close=Decimal(2))]


def test_candles_are_cached_per_symbol_timeframe_and_session_count() -> None:
    read_models._candle_cache.clear()
    storage = _CountingStorage()

    async def run() -> None:
        await read_models.get_price_candles_async(storage, "SPX", "5m", 5)
        await read_models.get_price_candles_async(storage, "SPX", "5m", 5)  # cached
        await read_models.get_price_candles_async(storage, "SPX", "15m", 5)  # other bucket
        await read_models.get_price_candles_async(storage, "SPX", "5m", 3)  # other session count
        await read_models.get_price_candles_async(storage, "spy", "5m", 5)  # other symbol

    asyncio.run(run())

    assert storage.calls == [("SPX", 300, 5), ("SPX", 900, 5), ("SPX", 300, 3), ("spy", 300, 5)]
    read_models._candle_cache.clear()


def test_a_future_is_refused_before_touching_the_storage() -> None:
    storage = _CountingStorage()
    try:
        asyncio.run(read_models.get_price_candles_async(storage, "NQ", "5m", 5))
    except NotFoundError:
        pass
    else:
        raise AssertionError("expected NotFoundError")
    assert storage.calls == []
