"""Diagnostics for httpx/httpcore transport failures (the '[WinError 10038] not a socket' cycles): log what is needed to tell the causes apart, never retry, never change the outcome."""

from __future__ import annotations

import httpx
import pytest

from backend.adapters.providers.thetadata.provider import ThetaDataProvider

REST_URL = "http://thetaterminal.test"
WS_URL = "ws://thetaterminal.test/v1/events"
PATH = "/v3/option/snapshot/greeks/first_order"


def _provider(handler) -> ThetaDataProvider:
    provider = ThetaDataProvider(REST_URL, WS_URL)
    provider._client = httpx.Client(base_url=REST_URL, transport=httpx.MockTransport(handler), timeout=10.0)
    return provider


def _failing_handler(calls: list[str]):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        raise httpx.ReadError("[WinError 10038] An operation was attempted on something that is not a socket", request=request)

    return handler


def test_a_transport_failure_is_logged_with_the_facts_and_still_raised_without_retry(caplog) -> None:
    calls: list[str] = []
    provider = _provider(_failing_handler(calls))

    with caplog.at_level("WARNING"), pytest.raises(httpx.ReadError):
        provider._get_json_allow_no_data(PATH, symbol="NDXP", expiration="*", format="json")

    assert calls == [PATH], "exactly one attempt: this is diagnostics, NOT a retry"
    lines = [r.getMessage() for r in caplog.records if "transport failure" in r.getMessage()]
    assert len(lines) == 1
    text = lines[0]
    assert PATH in text and "'symbol': 'NDXP'" in text and "'expiration': '*'" in text
    assert "ReadError" in text and "WinError 10038" in text
    assert "request_ran=" in text and "waited=" in text
    assert "timeout=10.0" in text, "the configured read timeout is logged so a failure near it stands out"
    assert "in_flight=1" in text and "pool=" in text and "thread=" in text


def test_the_in_flight_counter_returns_to_zero_after_a_failure_and_after_a_success() -> None:
    calls: list[str] = []
    provider = _provider(_failing_handler(calls))
    with pytest.raises(httpx.ReadError):
        provider._get_json_allow_no_data(PATH, symbol="SPX", expiration="*")
    assert provider._rest_in_flight == 0

    ok = _provider(lambda request: httpx.Response(200, json={"response": []}))
    ok._get_json_allow_no_data(PATH, symbol="SPX", expiration="*")
    assert ok._rest_in_flight == 0


def test_a_successful_request_logs_no_transport_failure(caplog) -> None:
    provider = _provider(lambda request: httpx.Response(200, json={"response": []}))

    with caplog.at_level("WARNING"):
        provider._get_json_allow_no_data(PATH, symbol="SPX", expiration="*")

    assert not [r for r in caplog.records if "transport failure" in r.getMessage()]


def test_only_transport_errors_are_logged_other_exceptions_pass_through_silently(caplog) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise ValueError("not a transport problem")

    provider = _provider(handler)
    with caplog.at_level("WARNING"), pytest.raises(ValueError):
        provider._get_json_allow_no_data(PATH, symbol="SPX", expiration="*")
    assert not [r for r in caplog.records if "transport failure" in r.getMessage()]
    assert provider._rest_in_flight == 0
