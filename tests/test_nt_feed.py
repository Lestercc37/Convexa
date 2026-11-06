from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.api.routes import futures_anchor_feed, nt_feed
from backend.domain.entities import GammaHeatmap, GammaHeatmapColumn
from backend.domain.use_cases import futures_proxy
from backend.domain.use_cases.futures_live import FuturesLiveBar
from backend.main import app

KEY = {"X-Convexa-Key": "test-key-not-real"}
NOW = datetime.now(UTC)


def _gamma(**levels):
    base = dict(as_of=NOW, gamma_flip=Decimal("7412.5"), call_wall=Decimal("7450"), put_wall=Decimal("7400"), absolute_gamma_strike=Decimal("7425"), max_pain=Decimal("7420"), net_gamma=Decimal("-5"))
    base.update(levels)
    return SimpleNamespace(**base)


SNAPSHOT = SimpleNamespace(
    price=Decimal("7431.25"),
    anchored_vwap=SimpleNamespace(value=Decimal("7418.3"), not_applicable=False),
    expected_move=SimpleNamespace(upper_bound=Decimal("7480"), lower_bound=Decimal("7380")),
    atr_range=None,
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    key_file = tmp_path / "key.txt"
    key_file.write_text("test-key-not-real", encoding="ascii")
    monkeypatch.setattr(futures_anchor_feed, "get_settings", lambda: SimpleNamespace(ninjatrader_anchor_key_file=str(key_file)))
    calls = {}

    async def gamma(storage, symbol, view="structural"):
        calls["gamma"] = (symbol, view)
        return _gamma()

    async def snapshot(storage, symbol):
        return SNAPSHOT

    async def heatmap(storage, symbol, view, metric, start, end, bucket, source):
        calls.setdefault("heatmap", []).append((symbol, view, metric, start, bucket, source))
        if source == "fast":
            return GammaHeatmap(symbol, view, metric, bucket, (), ())
        return GammaHeatmap(symbol, view, metric, bucket, (Decimal("7400"), Decimal("7405")), (GammaHeatmapColumn(NOW, (Decimal("1234567890"), None)),))

    async def anchor(storage, future, index):
        return Decimal("26.05")

    async def live(storage, future):
        return 31.2

    monkeypatch.setattr(nt_feed, "get_gamma_exposure_async", gamma)
    monkeypatch.setattr(nt_feed, "build_market_snapshot_async", snapshot)
    monkeypatch.setattr(nt_feed, "get_gamma_heatmap_async", heatmap)
    async def resolve(storage, future, index):
        return Decimal("31.2"), "ninjatrader", await anchor(storage, future, index), await live(storage, future)

    monkeypatch.setattr(nt_feed, "resolve_future_offset_async", resolve)
    futures_proxy._live_basis_cache.clear()
    with TestClient(app) as test_client:
        test_client.calls = calls
        yield test_client


def test_needs_the_key_and_a_known_future(client) -> None:
    assert client.get("/api/v1/feeds/nt/ES").status_code == 401
    assert client.get("/api/v1/feeds/nt/CL", headers=KEY).status_code == 404
    assert client.get("/api/v1/feeds/nt/ES?metric=bogus", headers=KEY).status_code == 422


def test_es_levels_and_heatmap_as_plain_text_lines(client) -> None:
    response = client.get("/api/v1/feeds/nt/MES?view=0dte_volume&metric=gamma", headers=KEY)
    assert response.status_code == 200
    lines = response.text.splitlines()
    assert lines[:2] == ["convexa 1", "symbol ES"]
    assert lines[2].startswith("as_of ")
    assert "basis 31.20 ninjatrader" in lines
    for expected in ("level gamma_flip 7412.50", "level call_wall 7450.00", "level put_wall 7400.00", "level abs 7425.00", "level avwap 7418.30", "level em_upper 7480.00", "dealer short_gamma"):
        assert expected in lines
    # The fast layer had nothing: the cycle's one-minute columns are sent instead (the header names the fast bucket).
    assert "heatmap 0dte_volume gamma 10" in lines
    assert {call[5] for call in client.calls["heatmap"]} == {"fast", "cycle"}
    # SPX strikes shifted by today's basis (+31.20); the previous session comes first, shifted by its own anchor (none here: today's).
    assert "strikes 7431.20 7436.20" in lines
    col = next(line for line in lines if line.startswith("col "))
    assert col.endswith(" 1.23457e+09 _")
    assert lines[-1] == "end"
    assert client.calls["gamma"] == ("ES", "0dte_volume")
    assert {call[0] for call in client.calls["heatmap"]} == {"SPX"}
    assert len([line for line in lines if line.startswith("strikes ")]) == 2  # today and the previous session


def test_since_asks_only_for_the_new_columns(client) -> None:
    from backend.domain.use_cases.market_hours import EASTERN_TIME

    # A moment inside today's heatmap window (08:30-16:15 ET): after 16:15 there are, correctly, no new columns to ask for.
    window_start = datetime.combine(NOW.astimezone(EASTERN_TIME).date(), datetime.min.time(), EASTERN_TIME).replace(hour=8, minute=31)
    if NOW < window_start:
        pytest.skip("before 08:31 ET today's heatmap window has not started")
    since = int(min(NOW - timedelta(seconds=30), window_start + timedelta(hours=7)).timestamp())
    client.get(f"/api/v1/feeds/nt/ES?since={since}", headers=KEY)
    starts = {call[3] for call in client.calls["heatmap"]}
    assert starts == {datetime.fromtimestamp(since, UTC)}


def test_rty_multiplies_iwm_by_the_live_ratio_and_needs_the_feed(client) -> None:
    assert client.get("/api/v1/feeds/nt/RTY", headers=KEY).status_code == 409
    storage = client.app.state.container.storage
    minute = (NOW - timedelta(minutes=2)).replace(second=0, microsecond=0)
    contract = "RTY 12-26" if NOW.date() < datetime(2026, 12, 10, tzinfo=UTC).date() else "RTY 03-27"
    for back in range(5):
        at = minute - timedelta(minutes=back)
        storage.save_futures_live_bars([FuturesLiveBar("RTY", contract, at, Decimal("2730"), Decimal("2731"), Decimal("2729"), Decimal("2730"), 5, "rth", Decimal("273"), at)])
    lines = client.get("/api/v1/feeds/nt/M2K", headers=KEY).text.splitlines()
    assert "basis_ratio 10.000000 ninjatrader" in lines
    assert "level gamma_flip 74125.00" in lines  # the fake IWM level x 10
    assert client.calls["gamma"][0] == "IWM"


def test_pressure_decays_by_half_every_15_minutes() -> None:
    from backend.domain.use_cases.flow_pressure import pressure_matrix

    start = datetime(2026, 11, 6, 14, 30, tzinfo=UTC)
    rows = [(start, 7400.0, 100.0), (start, 7400.0, 20.0), (start, 7405.0, -50.0)]
    strikes, minutes = pressure_matrix(rows, start, start + timedelta(minutes=16))
    assert strikes == [7400.0, 7405.0]
    assert minutes[0][1] == [120.0, -50.0]
    assert minutes[15][1][0] == pytest.approx(60.0)  # 15 minutes later: half
    assert len(minutes) == 16


def test_es_pressure_rows_in_futures_points(client, monkeypatch) -> None:
    from backend.domain.entities import MinuteStrikeFlow
    from backend.domain.use_cases.market_hours import EASTERN_TIME

    async def mapping(storage, future, source, now):
        return 1.0, 31.25

    monkeypatch.setattr(nt_feed, "price_mapping", mapping)
    nt_feed._pressure_cache.clear()
    storage = client.app.state.container.async_market_storage
    day_open = datetime.combine(datetime.now(UTC).astimezone(EASTERN_TIME).date(), datetime.min.time(), EASTERN_TIME).replace(hour=9, minute=30)
    if datetime.now(UTC) < day_open + timedelta(minutes=2):
        pytest.skip("before 09:32 ET there is no minute of today's session to test with")
    minute = day_open + timedelta(minutes=1)
    storage.minute_strike_flow.append(MinuteStrikeFlow(minute, "SPX", day_open.date(), 7400.0, "C", 80.0, 10, 0, 0, 0.0, 0.0))
    lines = client.get("/api/v1/feeds/nt/ES?pressure=true&heatmap=false", headers=KEY).text.splitlines()
    assert "pressure SPX 0dte 15" in lines
    assert "pstrikes 7431.20" in lines
    rows = [line for line in lines if line.startswith("prow ")]
    assert rows[0].endswith(" 0") and rows[1].endswith(" 80")
