"""Wildcard greeks snapshots (expiration="*") ask the Terminal only for expirations up to WILDCARD_MAX_DTE days; requests for one specific expiration
carry no max_dte, and the open-interest snapshots are untouched (max_dte is not verified there). 2026-10-15: the Terminal accepted max_dte on the real
SPXW root (HTTP 200, 29 expirations 0-43 days with max_dte=45)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import httpx

from backend.adapters.providers.thetadata import provider as provider_module
from tests.test_thetadata_provider import (
    _daily_bars_response,
    _first_order_entry,
    _open_interest_entry,
    _provider_with_transport,
)

FUTURE = (datetime.now(UTC).date() + timedelta(days=3)).isoformat()   # the near-the-money fetch drops expirations that are already over


def _recording_handler(seen: list[tuple[str, dict[str, str]]]):
    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        path = request.url.path
        if path == "/v3/option/snapshot/greeks/first_order":
            seen.append(("greeks", params))
            return httpx.Response(200, json={"response": [_first_order_entry("769.00", "CALL", expiration=FUTURE)]})
        if path == "/v3/option/snapshot/open_interest":
            seen.append(("oi", params))
            return httpx.Response(200, json={"response": [_open_interest_entry("769.00", "CALL", 100, expiration=FUTURE)]})
        if path == "/v3/interest_rate/history/eod":
            return httpx.Response(200, json={"response": [{"rate": 3.64, "created": "2026-08-31"}]})
        if path == "/v3/stock/history/eod":
            return httpx.Response(200, json=_daily_bars_response(base_close=769.0, daily_range=6.0))
        raise AssertionError(f"unexpected request: {request.url}")

    return handler


def test_the_wildcard_greeks_call_carries_max_dte() -> None:
    seen: list[tuple[str, dict[str, str]]] = []
    provider = _provider_with_transport(_recording_handler(seen))

    provider.get_option_chain("SPY")  # no expiration -> the nearest one, found through the wildcard snapshot

    greeks = [params for kind, params in seen if kind == "greeks"]
    assert greeks and all(params["expiration"] == "*" for params in greeks)
    assert all(params.get("max_dte") == str(provider_module.WILDCARD_MAX_DTE) for params in greeks)


def test_a_request_for_one_expiration_has_no_max_dte() -> None:
    seen: list[tuple[str, dict[str, str]]] = []
    provider = _provider_with_transport(_recording_handler(seen))

    provider.get_option_chain("SPY", expiration=date(2026, 9, 3))

    greeks = [params for kind, params in seen if kind == "greeks"]
    assert greeks and all(params["expiration"] == "2026-09-03" for params in greeks)
    assert all("max_dte" not in params for params in greeks)


def test_open_interest_snapshots_are_untouched() -> None:
    seen: list[tuple[str, dict[str, str]]] = []
    provider = _provider_with_transport(_recording_handler(seen))

    provider.get_option_chain("SPY")

    oi = [params for kind, params in seen if kind == "oi"]
    assert oi and all("max_dte" not in params for params in oi)


def test_the_limit_covers_the_widest_near_term_window() -> None:
    from backend.domain.use_cases import gamma

    widest = max(gamma.STRUCTURAL_WINDOW_DAYS_BY_SYMBOL.values())
    assert provider_module.WILDCARD_MAX_DTE >= widest + 15
