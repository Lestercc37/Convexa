"""The per-strike option flow kept by the stream processor (minute_strike_flow, migration 0050; the flow chain view, docs/diseno-cadena-flujo-2026-10-25.txt)."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from backend.adapters.storage.memory import InMemoryStorage
from backend.adapters.storage.sync_read_adapter import SyncStorageAsyncReadAdapter
from backend.core.settings import Settings
from backend.domain.entities import FlowEvent, FlowEventType, QuoteEvent, Side
from backend.domain.use_cases.minute_flow import MinuteFlowRecorder, _expiration_and_strike

CALL = "SPXW261019C07700000"
PUT = "SPXW261019P07650000"
T0 = datetime(2026, 10, 19, 14, 31, 10, tzinfo=UTC)  # 10:31:10 ET
LATER = (T0 + timedelta(minutes=5)).timestamp()


def _trade(
    occ: str, price: str, size: int = 10, condition: int | None = 18, at: datetime = T0
) -> FlowEvent:
    return FlowEvent(
        symbol="SPX",
        occ_symbol=occ,
        as_of=at,
        event_type=FlowEventType.UNUSUAL,
        premium=Decimal(price) * size * 100,
        size=size,
        aggressor_side=Side.UNKNOWN,
        condition=condition,
    )


def _recorder(strikes: bool = True) -> MinuteFlowRecorder:
    recorder = MinuteFlowRecorder(frozenset({"SPX"}))
    for occ in (CALL, PUT):
        recorder.observe_quote(
            QuoteEvent(
                symbol="SPX", occ_symbol=occ, as_of=T0, bid=Decimal("10.00"), ask=Decimal("10.50")
            )
        )
    recorder.set_deltas({CALL: 0.5, PUT: 0.4})
    if strikes:
        recorder.enable_strikes()
    return recorder


def test_expiration_and_strike_come_from_the_occ_symbol() -> None:
    assert _expiration_and_strike(CALL) == (date(2026, 10, 19), 7700.0)
    assert _expiration_and_strike("SPY261019P00767500") == (date(2026, 10, 19), 767.5)
    assert _expiration_and_strike("BAD") is None


def test_each_strike_and_right_gets_its_own_cell_with_the_bullish_sign_convention() -> None:
    recorder = _recorder()
    recorder.observe_trade(_trade(CALL, "10.50", size=10))  # buy call: +5
    recorder.observe_trade(_trade(CALL, "10.00", size=4))  # sell call: -2
    recorder.observe_trade(_trade(PUT, "10.50", size=10))  # buy put: -4
    rows = {(row.strike, row.option_right): row for row in recorder.take_strikes_before(LATER)}
    call, put = rows[(7700.0, "C")], rows[(7650.0, "P")]
    assert call.delta_flow == pytest.approx(3.0)
    assert put.delta_flow == pytest.approx(-4.0)
    assert (call.contracts_signed, call.buy_premium, call.sell_premium) == (14, 10_500.0, 4_000.0)
    assert call.expiration == date(2026, 10, 19)
    # The per-root rows are unchanged by the per-strike cells.
    (root_row,) = recorder.take_before(LATER)
    assert root_row.calls_delta_flow == pytest.approx(3.0)
    assert root_row.puts_delta_flow == pytest.approx(-4.0)


def test_packages_and_unsigned_trades_are_counted_apart_and_add_up_to_the_volume() -> None:
    recorder = _recorder()
    recorder.observe_trade(_trade(CALL, "10.50", size=10))  # signed
    recorder.observe_trade(_trade(CALL, "10.20", size=3, condition=132))  # multi-leg leg
    recorder.observe_trade(_trade(CALL, "10.25", size=2))  # inside the spread
    recorder.observe_trade(_trade(CALL, "10.50", size=5, condition=125))  # single-leg auction
    (cell,) = recorder.take_strikes_before(LATER)
    assert (cell.contracts_signed, cell.contracts_package, cell.contracts_other) == (10, 3, 7)
    assert cell.delta_flow == pytest.approx(5.0)


def test_strikes_are_off_unless_enabled_and_a_failed_flush_puts_the_cells_back() -> None:
    off = _recorder(strikes=False)
    off.observe_trade(_trade(CALL, "10.50"))
    assert off.take_strikes_before(LATER) == []
    assert Settings().minute_strike_flow_enabled is False

    recorder = _recorder()
    recorder.observe_trade(_trade(CALL, "10.50", size=10))
    rows = recorder.take_strikes_before(LATER)
    recorder.put_back_strikes(rows)
    recorder.observe_trade(_trade(CALL, "10.50", size=2))  # a late trade of the same minute
    (cell,) = recorder.take_strikes_before(LATER)
    assert cell.contracts_signed == 12
    assert cell.delta_flow == pytest.approx(6.0)


def test_only_finished_minutes_are_handed_over_and_the_storage_keeps_them() -> None:
    recorder = _recorder()
    recorder.observe_trade(_trade(CALL, "10.50", size=10))
    recorder.observe_trade(_trade(CALL, "10.50", size=10, at=T0 + timedelta(minutes=1)))
    first = recorder.take_strikes_before((T0 + timedelta(seconds=60)).timestamp())
    assert len(first) == 1
    storage = SyncStorageAsyncReadAdapter(InMemoryStorage())
    asyncio.run(storage.save_minute_strike_flow(first))
    assert storage.minute_strike_flow == first


def _cell(
    minute: datetime,
    strike: float,
    right: str,
    flow: float,
    expiration: date = date(2026, 10, 19),
    package: int = 0,
):
    from backend.domain.entities import MinuteStrikeFlow

    return MinuteStrikeFlow(
        minute=minute,
        symbol="SPX",
        expiration=expiration,
        strike=strike,
        option_right=right,
        delta_flow=flow,
        contracts_signed=10,
        contracts_package=package,
        contracts_other=5,
        buy_premium=0.0,
        sell_premium=0.0,
    )


def test_the_strikes_endpoint_sums_the_windows_per_strike_and_filters_the_expiration() -> None:
    from fastapi.testclient import TestClient

    from backend.main import app

    at = "2026-10-19T15:00:30Z"  # 11:00:30 ET: the windows end at 11:00
    end = datetime(2026, 10, 19, 15, 0, tzinfo=UTC)
    with TestClient(app) as client:
        storage = client.app.state.container.async_market_storage
        storage.minute_strike_flow.extend(
            [
                _cell(
                    end - timedelta(minutes=2), 7700.0, "C", 30.0, package=4
                ),  # inside every window
                _cell(end - timedelta(minutes=2), 7700.0, "P", -10.0),
                _cell(end - timedelta(minutes=20), 7700.0, "C", 50.0, package=7),  # 30 and 60 only
                _cell(end - timedelta(minutes=90), 7650.0, "P", -40.0),  # the day only
                _cell(end, 7700.0, "C", 999.0),  # the unfinished minute: never counted
                _cell(
                    end - timedelta(minutes=2), 7700.0, "C", 500.0, expiration=date(2026, 10, 20)
                ),  # 1DTE
            ]
        )
        zero = client.get(f"/api/v1/flow/SPX/strikes?at={at}").json()
        near = client.get(f"/api/v1/flow/SPX/strikes?at={at}&expiration=0-2dte").json()
        bad = client.get(f"/api/v1/flow/SPX/strikes?at={at}&expiration=weekly")
        storage.minute_strike_flow.clear()

    assert zero["expirations"] == ["2026-10-19"] and near["expirations"] == [
        "2026-10-19",
        "2026-10-20",
    ]
    rows = {row["strike"]: row for row in zero["strikes"]}
    k7700 = rows[7700.0]
    assert k7700["net"] == {"m5": 20.0, "m15": 20.0, "m30": 70.0, "m60": 70.0}
    assert k7700["calls"]["m30"] == 80.0 and k7700["puts"]["m5"] == -10.0
    assert k7700["packages_15m"] == 4
    assert rows[7650.0]["net"]["m60"] == 0.0 and rows[7650.0]["net_day"] == -40.0
    assert zero["net_day"] == 30.0
    assert zero["signed_share_day"] == round(40 / 71, 3)
    assert {row["strike"]: row for row in near["strikes"]}[7700.0]["net"]["m5"] == 520.0
    assert bad.status_code == 422


def test_the_strikes_endpoint_is_empty_before_the_open_and_reads_ndx_for_nq() -> None:
    from fastapi.testclient import TestClient

    from backend.main import app

    with TestClient(app) as client:
        early = client.get("/api/v1/flow/SPX/strikes?at=2026-10-19T13:00:00Z").json()  # 09:00 ET
        nq = client.get("/api/v1/flow/NQ/strikes?at=2026-10-19T15:00:00Z").json()
    assert early["strikes"] == [] and early["signed_share_day"] is None
    assert nq["symbol"] == "NQ" and nq["strikes"] == []


def test_nq_reads_qqq_flow_by_default_and_maps_each_strike_to_nq_price_units() -> None:
    from decimal import Decimal

    from fastapi.testclient import TestClient

    from backend.domain.entities import MarketPrice, MinuteStrikeFlow
    from backend.main import app

    end = datetime(2026, 10, 19, 15, 0, tzinfo=UTC)
    cell = MinuteStrikeFlow(
        minute=end - timedelta(minutes=2),
        symbol="QQQ",
        expiration=date(2026, 10, 19),
        strike=720.0,
        option_right="C",
        delta_flow=40.0,
        contracts_signed=10,
        contracts_package=0,
        contracts_other=0,
        buy_premium=0.0,
        sell_premium=0.0,
    )
    with TestClient(app) as client:
        storage = client.app.state.container.async_market_storage
        storage.minute_strike_flow.append(cell)
        sync = storage._storage
        sync.save_market_price(
            MarketPrice(
                symbol="NDX", as_of=end - timedelta(minutes=1), price=Decimal("29000"), volume=0
            )
        )
        sync.save_market_price(
            MarketPrice(
                symbol="QQQ", as_of=end - timedelta(minutes=1), price=Decimal("725"), volume=0
            )
        )
        nq = client.get("/api/v1/flow/NQ/strikes?at=2026-10-19T15:00:30Z").json()
        ndx_own = client.get(
            "/api/v1/flow/NQ/strikes?at=2026-10-19T15:00:30Z&flow_source=NDX"
        ).json()
        storage.minute_strike_flow.clear()

    assert nq["flow_source"] == "QQQ" and nq["price_ratio"] == 40.0
    (row,) = nq["strikes"]
    assert (
        row["strike"] == 720.0 and row["price"] == 28_800.0
    )  # 720 x 29000 / 725, no NQ anchor typed -> offset 0
    assert row["net"]["m5"] == 40.0
    assert (
        ndx_own["flow_source"] == "NDX"
        and ndx_own["price_ratio"] == 1.0
        and ndx_own["strikes"] == []
    )


def test_on_a_weekend_the_chain_falls_back_to_the_latest_session_with_flow() -> None:
    from backend.domain.use_cases.strike_flow import latest_session_with_flow

    storage = SyncStorageAsyncReadAdapter(InMemoryStorage())
    friday = datetime(2026, 10, 30, 15, 0, tzinfo=UTC)  # 11:00 ET
    storage.minute_strike_flow.append(_cell(friday, 7600.0, "C", 12.0, expiration=date(2026, 10, 30)))
    sunday = datetime(2026, 11, 1, 18, 0, tzinfo=UTC)

    found = asyncio.run(latest_session_with_flow(storage, "SPX", sunday, 7))
    assert found is not None
    close, traded = found
    assert close == datetime(2026, 10, 30, 20, 0, tzinfo=UTC)  # 16:00 ET (EDT)
    assert traded == [date(2026, 10, 30)]
    # Nothing within the lookback: no fallback.
    assert asyncio.run(latest_session_with_flow(storage, "SPX", sunday + timedelta(days=14), 7)) is None
    assert asyncio.run(latest_session_with_flow(storage, "NDX", sunday, 7)) is None


def test_the_5_s_partial_flushes_add_up_exactly_to_the_per_minute_totals() -> None:
    # The fast flush (take_strikes_all every 5 s, the current minute included) against one write per finished minute: same rows once the upsert adds them up.
    trades = [
        _trade(CALL, "10.50", size=10),
        _trade(CALL, "10.00", size=4, at=T0 + timedelta(seconds=20)),
        _trade(PUT, "10.50", size=10, at=T0 + timedelta(seconds=30)),
        _trade(CALL, "10.20", size=3, condition=132, at=T0 + timedelta(seconds=40)),
        _trade(CALL, "10.50", size=7, at=T0 + timedelta(minutes=1, seconds=5)),
        _trade(PUT, "10.00", size=6, at=T0 + timedelta(minutes=1, seconds=15)),
    ]
    whole = _recorder()
    for trade in trades:
        whole.observe_trade(trade)
    per_minute = whole.take_strikes_before(LATER)

    fast, pieces = _recorder(), []
    for trade in trades:
        fast.observe_trade(trade)
        pieces.append(fast.take_strikes_all())  # a flush after every trade: the current minute is handed over too
    assert fast.take_strikes_all() == []
    storage = SyncStorageAsyncReadAdapter(InMemoryStorage())
    for piece in pieces:
        asyncio.run(storage.save_minute_strike_flow(piece))

    def totals(rows):
        out: dict = {}
        for row in rows:
            key = (row.minute, row.expiration, row.strike, row.option_right)
            acc = out.setdefault(key, [0.0, 0, 0, 0, 0.0, 0.0])
            for i, value in enumerate((row.delta_flow, row.contracts_signed, row.contracts_package, row.contracts_other, row.buy_premium, row.sell_premium)):
                acc[i] += value
        return {key: [round(v, 6) for v in acc] for key, acc in out.items()}

    assert len(per_minute) == 4  # 2 minutes x (call, put)
    assert totals(storage.minute_strike_flow) == totals(per_minute)


def test_the_live_strikes_endpoint_includes_the_current_partial_minute(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    import backend.api.routes.options as options_route
    from backend.main import app

    now = datetime(2026, 10, 19, 15, 0, 30, tzinfo=UTC)  # 11:00:30 ET

    class _Now(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz) if tz else now.replace(tzinfo=None)

    monkeypatch.setattr(options_route, "datetime", _Now)
    minute = datetime(2026, 10, 19, 15, 0, tzinfo=UTC)
    with TestClient(app) as client:
        storage = client.app.state.container.async_market_storage
        storage.minute_strike_flow.extend(
            [
                _cell(minute, 7700.0, "C", 25.0),  # the current minute, written by the 5 s flush
                _cell(minute - timedelta(minutes=4), 7700.0, "C", 5.0),  # still inside the 5-minute window
                _cell(minute - timedelta(minutes=5), 7700.0, "C", 100.0),  # 15-minute window, not the 5
            ]
        )
        live = client.get("/api/v1/flow/SPX/strikes").json()
        replay = client.get("/api/v1/flow/SPX/strikes?at=2026-10-19T15:00:30Z").json()
        storage.minute_strike_flow.clear()

    (row,) = live["strikes"]
    assert row["net"]["m5"] == 30.0 and row["net"]["m15"] == 130.0 and row["net_day"] == 130.0
    assert live["as_of"] == "2026-10-19T15:00:30Z"  # now, not the start of the minute
    # The replay keeps finished minutes only.
    (old,) = replay["strikes"]
    assert old["net"]["m5"] == 105.0 and old["net_day"] == 105.0
