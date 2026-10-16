"""The AM-settled index root (SPX/NDX/VIX monthly) settles at the open of its expiration day: its contracts must not stay in the chain that day,
while the weekly PM root and the same root's later expirations stay. Found on the 2026-10-16 monthly OPEX."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import httpx

from backend.adapters.providers.thetadata import provider as provider_module
from tests.test_thetadata_provider import _provider_with_transport


def _entry(root: str, expiration: date, strike: float = 7700.0) -> dict:
    return {
        "contract": {"symbol": root, "strike": strike, "right": "CALL", "expiration": expiration.isoformat()},
        "data": [{"timestamp": "2026-10-16T11:00:00", "underlying_price": 7740.0}],
    }


def _provider():
    return _provider_with_transport(lambda request: httpx.Response(500))


def test_the_am_root_is_dropped_on_its_expiration_day_only(monkeypatch) -> None:
    today = datetime.now(provider_module.EASTERN_TIME).date()
    tomorrow = today + timedelta(days=1)
    monkeypatch.setattr(provider_module, "_nearest_expiration_cutoff", lambda now: today)
    entries = [_entry("SPX", today), _entry("SPXW", today), _entry("SPX", tomorrow), _entry("SPXW", tomorrow)]

    kept = _provider()._fetch_near_the_money_entries_all("SPX", entries)

    kept_keys = {(e["contract"]["symbol"], e["contract"]["expiration"]) for e in kept}
    assert kept_keys == {("SPXW", today.isoformat()), ("SPX", tomorrow.isoformat()), ("SPXW", tomorrow.isoformat())}


def test_symbols_without_a_weekly_root_are_untouched(monkeypatch) -> None:
    today = datetime.now(provider_module.EASTERN_TIME).date()
    monkeypatch.setattr(provider_module, "_nearest_expiration_cutoff", lambda now: today)
    entries = [_entry("SPY", today, 770.0)]

    kept = _provider()._fetch_near_the_money_entries_all("SPY", entries)

    assert len(kept) == 1
