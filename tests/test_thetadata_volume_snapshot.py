"""ThetaDataProvider.get_option_volume_snapshot: the day's volume per contract of one expiration (GET /v3/option/snapshot/ohlc, one request per root)."""

from __future__ import annotations

from datetime import date

import httpx
import pytest

from tests.test_thetadata_provider import _provider_with_transport

EXPIRATION = date(2026, 10, 16)


def _entry(root: str, strike: float, right: str, volume: int | None) -> dict:
    data = [] if volume is None else [{"volume": volume, "open": 1.0, "close": 1.1}]
    return {"contract": {"symbol": root, "expiration": EXPIRATION.isoformat(), "strike": strike, "right": right}, "data": data}


def test_the_snapshot_maps_each_traded_contract_to_its_occ_symbol_and_skips_the_untraded() -> None:
    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v3/option/snapshot/ohlc"
        params = dict(request.url.params)
        seen.append(params)
        if params["symbol"] == "SPX":
            return httpx.Response(472, text="No data found for your request")  # the AM root lists no such date
        return httpx.Response(
            200,
            json={
                "response": [
                    _entry("SPXW", 7550.0, "PUT", 7229),
                    _entry("SPXW", 7600.0, "CALL", 0),
                    _entry("SPXW", 7650.0, "PUT", None),
                    _entry("SPXW", 7700.0, "C", 31),
                ]
            },
        )

    volumes = _provider_with_transport(handler).get_option_volume_snapshot("SPX", EXPIRATION)

    assert volumes == {"SPXW261016P07550000": 7229, "SPXW261016C07700000": 31}
    assert [params["symbol"] for params in seen] == ["SPX", "SPXW"]
    assert all(params["expiration"] == "2026-10-16" and params["strike_range"] == "100" for params in seen)


def test_a_symbol_with_one_root_makes_one_request() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(dict(request.url.params)["symbol"])
        return httpx.Response(200, json={"response": [_entry("SPY", 770.0, "CALL", 120)]})

    assert _provider_with_transport(handler).get_option_volume_snapshot("SPY", EXPIRATION) == {"SPY261016C00770000": 120}
    assert calls == ["SPY"]


def test_a_real_failure_raises_instead_of_returning_a_partial_figure() -> None:
    provider = _provider_with_transport(lambda request: httpx.Response(503, text="io exception"))
    with pytest.raises(RuntimeError):
        provider.get_option_volume_snapshot("SPY", EXPIRATION)
