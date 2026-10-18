"""GET /gamma/{symbol}/heatmap: a read-only strike x time matrix of a stored per-strike figure (gamma_aggregate_items).
The SQL itself is exercised against a real Postgres in test_postgresql_integration.py; here: validation, the response shape,
the serializer's scale, and the ES/NQ proxy shift."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient

from backend.api.serializers import gamma_heatmap_response
from backend.domain.entities import GammaHeatmap, GammaHeatmapColumn
from backend.domain.use_cases.read_models import get_gamma_heatmap_async
from backend.main import app

T0 = datetime(2026, 10, 16, 14, 0, tzinfo=UTC)


def _heatmap() -> GammaHeatmap:
    return GammaHeatmap(
        symbol="SPX",
        view="tactical",
        metric="gamma",
        bucket_seconds=120,
        strikes=(Decimal(7700), Decimal(7705), Decimal("7710.5")),
        columns=(
            GammaHeatmapColumn(as_of=T0, values=(Decimal(-10), Decimal(40), None)),
            GammaHeatmapColumn(as_of=T0 + timedelta(minutes=2), values=(Decimal(5), Decimal(-80), Decimal("20.5"))),
        ),
    )


def test_serializer_keeps_the_matrix_aligned_and_reports_the_p98_of_magnitudes() -> None:
    payload = gamma_heatmap_response(_heatmap())

    assert payload["strikes"] == [7700, 7705, 7710.5]
    assert payload["columns"][0] == {"as_of": "2026-10-16T14:00:00Z", "values": [-10, 40, None]}
    assert payload["columns"][1]["values"] == [5, -80, 20.5]
    # |values| sorted: 5, 10, 20.5, 40, 80 -> index int(0.98 * 5) = 4 -> 80
    assert payload["scale_p98"] == 80


def test_serializer_scale_is_none_for_an_empty_matrix() -> None:
    empty = GammaHeatmap(symbol="SPX", view="tactical", metric="gamma", bucket_seconds=120, strikes=(), columns=())
    assert gamma_heatmap_response(empty)["scale_p98"] is None


def test_endpoint_answers_an_empty_matrix_when_nothing_is_stored() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/gamma/SPX/heatmap")

    assert response.status_code == 200
    body = response.json()
    assert body["symbol"] == "SPX"
    assert (body["view"], body["metric"], body["bucket_seconds"]) == ("tactical", "gamma", 120)
    assert body["strikes"] == [] and body["columns"] == [] and body["scale_p98"] is None


def test_endpoint_rejects_bad_ranges_and_parameters() -> None:
    with TestClient(app) as client:
        cases = {
            "end before start": "?start=2026-10-16T15:00:00Z&end=2026-10-16T14:00:00Z",
            "more than 5 days": "?start=2026-10-01T00:00:00Z&end=2026-10-10T00:00:00Z",
            "no timezone": "?start=2026-10-16T14:00:00&end=2026-10-16T15:00:00",
            "bucket too small": "?bucket_seconds=10",
            "unknown metric": "?metric=vanna",
            "unknown view": "?view=weekly",
        }
        for name, query in cases.items():
            assert client.get(f"/api/v1/gamma/SPX/heatmap{query}").status_code == 422, name


def test_futures_heatmap_needs_todays_opening_price() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/gamma/ES/heatmap")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "NO_OPENING_PRICE"


def test_futures_heatmap_covers_the_current_et_date_only() -> None:
    with TestClient(app) as client:
        week_ago = (datetime.now(UTC) - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
        response = client.get(f"/api/v1/gamma/NQ/heatmap?start={week_ago}")

    assert response.status_code == 422


class _FakeStorage:
    """Async storage stand-in: SPX has a stored matrix, the opening-price offset of ES is +48.5 points."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    async def get_gamma_heatmap(self, underlying, view, metric, start, end, bucket_seconds):
        self.asked.append(underlying)
        return _heatmap()

    async def get_future_price_anchor(self, symbol, session_date):
        return Decimal("7790")

    async def get_price_history(self, underlying, start, end):
        from backend.domain.entities import MarketPrice

        return [MarketPrice(symbol=underlying, as_of=T0, price=Decimal("7741.5"), volume=0)]

    async def get_latest_price(self, underlying):
        from backend.domain.entities import MarketPrice

        return MarketPrice(symbol=underlying, as_of=T0, price=Decimal("7741.5"), volume=0)


def test_a_stock_or_index_is_read_as_is() -> None:
    storage = _FakeStorage()
    heatmap = asyncio.run(get_gamma_heatmap_async(storage, "SPX", "tactical", "gamma", T0, T0 + timedelta(hours=1), 120))
    assert storage.asked == ["SPX"]
    assert heatmap.strikes == _heatmap().strikes


def test_a_future_reads_its_proxy_and_shifts_every_strike_by_the_opening_price_offset() -> None:
    storage = _FakeStorage()
    heatmap = asyncio.run(get_gamma_heatmap_async(storage, "ES", "tactical", "gamma", T0, T0 + timedelta(hours=1), 120))

    assert storage.asked == ["SPX"]
    assert heatmap.symbol == "ES"
    # anchor 7790 - SPX opening reference 7741.5 = +48.5 points
    assert heatmap.strikes == (Decimal("7748.5"), Decimal("7753.5"), Decimal("7759"))
    assert heatmap.columns == _heatmap().columns  # exposures are the index's own, only the strikes move
