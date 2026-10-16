"""A two-root index (SPX/SPXW) is asked on both roots, but a root that answered 472 ("no data") for one
specific expiration is not asked for it again for a while. On 2026-10-15 the API spent about 4 requests a
minute on SPX-root calls answered 472 for today's 0DTE (SPX's own root only lists the AM monthlies)."""

from __future__ import annotations

from datetime import date

import httpx

from backend.adapters.providers.thetadata import provider as provider_module
from tests.test_thetadata_provider import (
    _daily_bars_response,
    _first_order_entry,
    _open_interest_entry,
    _provider_with_transport,
)

EXPIRATION = date(2026, 9, 3)


def _handler(calls: list[tuple[str, str]], *, spx_status: int = 472):
    def handler(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        symbol = params.get("symbol")
        path = request.url.path
        if path in ("/v3/option/snapshot/greeks/first_order", "/v3/option/snapshot/open_interest"):
            calls.append((path.rsplit("/", 1)[-1], str(symbol)))
            if symbol == "SPX":
                if spx_status == 472:
                    return httpx.Response(472, text="No data found for your request")
                return httpx.Response(200, json={"response": []})
            if path.endswith("first_order"):
                return httpx.Response(
                    200, json={"response": [_first_order_entry("7700", "CALL", expiration="2026-09-03", root="SPXW")]}
                )
            return httpx.Response(
                200, json={"response": [_open_interest_entry("7700", "CALL", 250, root="SPXW")]}
            )
        if path == "/v3/interest_rate/history/eod":
            return httpx.Response(200, json={"response": [{"rate": 3.64, "created": "2026-08-31"}]})
        if path == "/v3/index/history/eod":
            return httpx.Response(200, json=_daily_bars_response(base_close=7700.0, daily_range=50.0))
        raise AssertionError(f"unexpected request: {request.url}")

    return handler


def _fetch_twice(provider) -> None:
    provider.get_option_chain("SPX", expiration=EXPIRATION)
    provider._near_the_money_cache.clear()  # the 10 s response cache is not what is under test
    provider._open_interest_cache.clear()
    provider.get_option_chain("SPX", expiration=EXPIRATION)


def test_the_root_that_said_472_is_not_asked_again_but_the_other_root_still_is() -> None:
    calls: list[tuple[str, str]] = []
    provider = _provider_with_transport(_handler(calls))

    _fetch_twice(provider)

    assert calls.count(("first_order", "SPX")) == 1
    # The greeks call already learned SPX has no such date, so even the first open-interest call skips it.
    assert calls.count(("open_interest", "SPX")) == 0
    assert calls.count(("first_order", "SPXW")) == 2
    assert calls.count(("open_interest", "SPXW")) == 2


def test_the_root_is_asked_again_once_the_short_memory_expires(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []
    provider = _provider_with_transport(_handler(calls))
    clock = [1_000.0]
    monkeypatch.setattr(provider_module.time, "monotonic", lambda: clock[0])

    provider.get_option_chain("SPX", expiration=EXPIRATION)
    clock[0] += provider_module.ROOT_WITHOUT_EXPIRATION_TTL_SECONDS + 1
    provider._near_the_money_cache.clear()
    provider._open_interest_cache.clear()
    provider.get_option_chain("SPX", expiration=EXPIRATION)

    assert calls.count(("first_order", "SPX")) == 2


def test_an_empty_200_is_not_remembered_only_a_472_is() -> None:
    """An empty 200 can be a Terminal that is momentarily overloaded: it must be asked again."""
    calls: list[tuple[str, str]] = []
    provider = _provider_with_transport(_handler(calls, spx_status=200))

    _fetch_twice(provider)

    assert calls.count(("first_order", "SPX")) == 2
