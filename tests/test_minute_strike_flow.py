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


def _trade(occ: str, price: str, size: int = 10, condition: int | None = 18, at: datetime = T0) -> FlowEvent:
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
        recorder.observe_quote(QuoteEvent(symbol="SPX", occ_symbol=occ, as_of=T0, bid=Decimal("10.00"), ask=Decimal("10.50")))
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
