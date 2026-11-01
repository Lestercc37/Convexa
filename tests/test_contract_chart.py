"""Call | Put chart of one 0DTE strike from option_quote_minutes (backend/domain/use_cases/contract_chart.py, GET /contracts/{symbol}/minutes)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from backend.domain.entities import OptionQuoteMinute
from backend.domain.use_cases.contract_chart import (
    build_series,
    default_strike,
    occ_symbol,
    parse_occ,
    root_for,
    strikes_from_symbols,
)

DAY = date(2026, 10, 30)
OPEN = datetime(2026, 10, 30, 13, 30, tzinfo=UTC)  # 09:30 ET


def _quote(minute: datetime, occ: str, bid: str, ask: str) -> OptionQuoteMinute:
    return OptionQuoteMinute(minute, occ, Decimal(bid), Decimal(ask), minute + timedelta(seconds=50))


def test_roots_and_occ_symbols_follow_the_0dte_roots() -> None:
    assert root_for("SPX") == "SPXW"
    assert root_for("ES") == "SPXW"
    assert root_for("NQ") == "NDXP"
    assert root_for("qqq") == "QQQ"
    assert occ_symbol("SPXW", DAY, "C", 7630) == "SPXW261030C07630000"
    assert occ_symbol("IWM", DAY, "P", 277.5) == "IWM261030P00277500"
    assert parse_occ("IWM261030P00277500") == ("IWM", DAY, "P", 277.5)
    assert parse_occ("not an occ") is None


def test_only_strikes_with_both_sides_of_that_day_are_listed() -> None:
    symbols = ["SPXW261030C07630000", "SPXW261030P07630000", "SPXW261030C07635000", "SPXW261102P07630000", "SPY261030C00700000"]
    assert strikes_from_symbols(symbols, "SPXW", DAY) == (7630.0,)


def test_dollars_per_contract_start_at_zero_and_skip_unusable_quotes() -> None:
    occ = "SPXW261030C07630000"
    series = build_series(
        occ,
        "C",
        [
            _quote(OPEN + timedelta(minutes=2), occ, "4.00", "4.20"),
            _quote(OPEN, occ, "3.00", "3.20"),
            _quote(OPEN + timedelta(minutes=1), occ, "3.50", "0"),  # no offer: skipped
        ],
    )
    assert [p.pnl for p in series.points] == [0.0, 100.0]
    assert series.points[1].mid == 4.10


def test_the_default_strike_is_the_at_the_money_one() -> None:
    strikes = (7620.0, 7630.0, 7640.0)
    mids = {7620.0: (15.0, 5.0), 7630.0: (9.0, 8.5), 7640.0: (4.0, 13.0)}
    assert default_strike(strikes, mids) == 7630.0
    assert default_strike(strikes, {}) == 7630.0
    assert default_strike((), {}) is None


def test_the_endpoint_returns_both_sides_of_the_requested_and_the_default_strike() -> None:
    from fastapi.testclient import TestClient

    from backend.main import app

    rows = []
    for k, (c, p) in {7630: ("9.00", "8.60"), 7640: ("4.00", "13.00")}.items():
        call, put = occ_symbol("SPXW", DAY, "C", k), occ_symbol("SPXW", DAY, "P", k)
        rows += [_quote(OPEN, call, c, str(Decimal(c) + Decimal("0.2"))), _quote(OPEN, put, p, str(Decimal(p) + Decimal("0.2")))]
        rows += [_quote(OPEN + timedelta(minutes=5), call, str(Decimal(c) + 1), str(Decimal(c) + Decimal("1.2")))]
    with TestClient(app) as client:
        storage = client.app.state.container.async_market_storage
        storage.option_quote_minutes.extend(rows)
        default = client.get("/api/v1/contracts/SPX/minutes?date=2026-10-30").json()
        chosen = client.get("/api/v1/contracts/ES/minutes?date=2026-10-30&strike=7640").json()
        empty = client.get("/api/v1/contracts/SPX/minutes?date=2026-10-29").json()
        storage.option_quote_minutes.clear()

    assert default["root"] == "SPXW"
    assert default["strikes"] == [7630, 7640]
    assert default["strike"] == 7630
    assert [p["pnl"] for p in default["call"]["points"]] == [0, 100]
    assert default["put"]["occ_symbol"] == "SPXW261030P07630000"
    assert chosen["strike"] == 7640 and chosen["symbol"] == "ES"
    assert empty["strikes"] == [] and empty["call"] is None
