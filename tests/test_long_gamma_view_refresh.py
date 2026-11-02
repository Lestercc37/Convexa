"""The 90-day gamma view refresh (refresh_snapshot._refresh_long_view_impl): its own max_dte=90 fetch, at most every 15 minutes per symbol,
never for symbols outside the tactical scope, and a failure never breaks the cycle."""

from __future__ import annotations

from types import SimpleNamespace

from backend.domain.use_cases import refresh_snapshot as rs


class _Provider:
    def __init__(self, fail: bool = False) -> None:
        self.calls: list[tuple[str, int | None]] = []
        self.fail = fail

    def get_option_chain(self, symbol, expiration=None, max_dte=None):
        self.calls.append((symbol, max_dte))
        if self.fail:
            raise RuntimeError("terminal down")
        return SimpleNamespace(contracts=(1, 2, 3))


class _Storage:
    def __init__(self) -> None:
        self.saved = []

    def get_daily_bars(self, symbol, limit):
        return []

    def save_gamma_aggregate(self, aggregate):
        self.saved.append(aggregate)


class _Orchestrator:
    def build_long_view(self, symbol, chain, bars):
        return SimpleNamespace(view="90d", symbol=symbol)


def _use_case(provider, enabled=True):
    return SimpleNamespace(market_data_provider=provider, storage=_Storage(), gamma_exposure_orchestrator=_Orchestrator(), long_view_enabled=enabled)


def test_fetches_with_max_dte_90_once_per_15_minutes_and_skips_other_symbols(monkeypatch) -> None:
    rs._long_view_refreshed_at.clear()
    clock = [1000.0]
    monkeypatch.setattr(rs.time, "monotonic", lambda: clock[0])
    provider = _Provider()
    use_case = _use_case(provider)
    rs._refresh_long_view_impl(use_case, "spx")
    rs._refresh_long_view_impl(use_case, "SPX")  # same slot: nothing
    rs._refresh_long_view_impl(use_case, "AAPL")  # outside the tactical scope
    clock[0] += 15 * 60
    rs._refresh_long_view_impl(use_case, "SPX")
    assert provider.calls == [("SPX", 90), ("SPX", 90)]
    assert [a.view for a in use_case.storage.saved] == ["90d", "90d"]


def test_a_failure_is_logged_and_waits_for_the_next_slot(monkeypatch) -> None:
    rs._long_view_refreshed_at.clear()
    monkeypatch.setattr(rs.time, "monotonic", lambda: 5000.0)
    provider = _Provider(fail=True)
    use_case = _use_case(provider)
    rs._refresh_long_view_impl(use_case, "NDX")
    rs._refresh_long_view_impl(use_case, "NDX")
    assert provider.calls == [("NDX", 90)] and use_case.storage.saved == []


def test_switched_off_fetches_nothing() -> None:
    rs._long_view_refreshed_at.clear()
    provider = _Provider()
    rs._refresh_long_view_impl(_use_case(provider, enabled=False), "SPX")
    assert provider.calls == []
