"""The signed option delta flow per ET minute kept by the stream processor (migration 0048, use_cases/minute_flow.py)."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from backend import stream_processor_worker as worker
from backend.adapters.storage.memory import InMemoryStorage
from backend.adapters.storage.sync_read_adapter import SyncStorageAsyncReadAdapter
from backend.domain.entities import FlowEvent, FlowEventType, MinuteOptionFlow, QuoteEvent, Side
from backend.domain.use_cases.minute_flow import MinuteFlowRecorder, _root_and_right
from backend.stream_processor_worker import _handle_raw_frame

CALL = "SPXW261019C07700000"
PUT = "SPXW261019P07700000"
T0 = datetime(2026, 10, 19, 14, 31, 10, tzinfo=UTC)  # 10:31:10 ET


def _quote(occ: str, bid: str = "10.00", ask: str = "10.50", symbol: str = "SPX") -> QuoteEvent:
    return QuoteEvent(symbol=symbol, occ_symbol=occ, as_of=T0, bid=Decimal(bid), ask=Decimal(ask))


def _trade(occ: str, price: str, size: int = 10, condition: int | None = 18, symbol: str = "SPX", at: datetime = T0) -> FlowEvent:
    return FlowEvent(
        symbol=symbol,
        occ_symbol=occ,
        as_of=at,
        event_type=FlowEventType.UNUSUAL,
        premium=Decimal(price) * size * 100,
        size=size,
        aggressor_side=Side.UNKNOWN,
        condition=condition,
    )


def _recorder(delta: float = 0.5) -> MinuteFlowRecorder:
    recorder = MinuteFlowRecorder(frozenset({"SPX", "SPY"}))
    recorder.observe_quote(_quote(CALL))
    recorder.observe_quote(_quote(PUT))
    recorder.set_deltas({CALL: delta, PUT: delta})
    return recorder


def _rows(recorder: MinuteFlowRecorder) -> list[MinuteOptionFlow]:
    return recorder.take_before((T0 + timedelta(minutes=5)).timestamp())


def test_root_and_right_come_from_the_occ_symbol() -> None:
    assert _root_and_right(CALL) == ("SPXW", "C")
    assert _root_and_right(PUT) == ("SPXW", "P")
    assert _root_and_right("SPY261019P00767000") == ("SPY", "P")


def test_buying_a_call_at_the_ask_is_positive_and_selling_it_at_the_bid_is_negative() -> None:
    recorder = _recorder(delta=0.5)
    recorder.observe_trade(_trade(CALL, "10.50", size=10))  # at the ask: buy
    recorder.observe_trade(_trade(CALL, "10.00", size=4))  # at the bid: sell
    (row,) = _rows(recorder)
    assert row.calls_delta_flow == pytest.approx(10 * 0.5 - 4 * 0.5)
    assert row.puts_delta_flow == 0
    assert (row.trades_classified, row.trades_unclassified, row.contracts_classified, row.contracts_total) == (2, 0, 14, 14)


def test_buying_a_put_is_negative_and_selling_a_put_is_positive() -> None:
    recorder = _recorder(delta=0.4)
    recorder.observe_trade(_trade(PUT, "10.60", size=10))  # beyond the ask: buy a put
    recorder.observe_trade(_trade(PUT, "9.90", size=5))  # beyond the bid: sell a put
    (row,) = _rows(recorder)
    assert row.puts_delta_flow == pytest.approx(-10 * 0.4 + 5 * 0.4)
    assert row.calls_delta_flow == 0


def test_a_trade_inside_the_spread_or_with_another_condition_or_no_quote_or_no_delta_has_no_side() -> None:
    recorder = _recorder()
    recorder.observe_trade(_trade(CALL, "10.25", size=3))  # inside the spread
    recorder.observe_trade(_trade(CALL, "10.50", size=6, condition=130))  # multi-leg: not a simple trade
    recorder.observe_trade(_trade(CALL, "10.50", size=7, condition=None))
    recorder.observe_trade(_trade("SPXW261019C07705000", "5.0", size=2))  # no quote, no delta for this contract
    (row,) = _rows(recorder)
    assert row.calls_delta_flow == 0 and row.puts_delta_flow == 0
    assert (row.trades_classified, row.trades_unclassified) == (0, 4)
    assert (row.contracts_classified, row.contracts_total) == (0, 18)  # the share of volume that carries a side is always visible


def test_other_underlyings_are_ignored_and_the_roots_of_one_underlying_are_kept_apart() -> None:
    recorder = _recorder()
    recorder.observe_quote(_quote("SPX261019C07700000", symbol="SPX"))
    recorder.set_deltas({"SPX261019C07700000": 0.5})
    recorder.observe_trade(_trade(CALL, "10.50", size=2))
    recorder.observe_trade(_trade("SPX261019C07700000", "10.50", size=2))
    recorder.observe_trade(_trade("VIX261021C00020000", "1.0", size=100, symbol="VIX"))
    rows = _rows(recorder)
    assert sorted(row.root for row in rows) == ["SPX", "SPXW"]
    assert all(row.symbol == "SPX" for row in rows)


def test_the_minute_is_the_exchange_minute_and_only_finished_minutes_are_handed_over() -> None:
    recorder = _recorder()
    recorder.observe_trade(_trade(CALL, "10.50", size=1), exchange_time=T0)  # 14:31
    recorder.observe_trade(_trade(CALL, "10.50", size=1), exchange_time=T0 + timedelta(minutes=1))  # 14:32
    first = recorder.take_before(T0.replace(second=0).timestamp() + 60 + 1)  # now = 14:32:01 -> only 14:31 is finished
    assert [row.minute.astimezone(UTC).minute for row in first] == [31]
    assert first[0].minute.utcoffset() is not None
    second = recorder.take_before(T0.replace(second=0).timestamp() + 120 + 1)
    assert [row.minute.astimezone(UTC).minute for row in second] == [32]
    assert recorder.take_before(T0.timestamp() + 3600) == []


def test_a_failed_flush_puts_its_minutes_back_and_a_late_trade_adds_to_the_same_minute() -> None:
    recorder = _recorder()
    recorder.observe_trade(_trade(CALL, "10.50", size=2))
    rows = _rows(recorder)
    recorder.observe_trade(_trade(CALL, "10.50", size=4))  # arrives after the flush took the minute
    recorder.put_back(rows)
    (merged,) = _rows(recorder)
    assert merged.calls_delta_flow == pytest.approx((2 + 4) * 0.5)
    assert merged.contracts_total == 6


def test_the_recorder_through_the_real_raw_frames() -> None:
    state = worker._ProcessorState(_FakeRelay(), _FakePrice())  # type: ignore[arg-type]
    state.minute_flow_enabled = True
    state.minute_flow = MinuteFlowRecorder(frozenset({"SPX"}))
    state.minute_flow.set_deltas({CALL: 0.25})
    contract = {"security_type": "OPTION", "root": "SPXW", "expiration": 20261019, "strike": 7700000, "right": "C"}
    _handle_raw_frame(state, json.dumps({"header": {"type": "QUOTE"}, "contract": contract, "quote": {"bid": 10.0, "ask": 10.5, "date": 20261019, "ms_of_day": 37870000}}))
    _handle_raw_frame(state, json.dumps({"header": {"type": "TRADE"}, "contract": contract, "trade": {"size": 8, "price": 10.5, "condition": 18, "date": 20261019, "ms_of_day": 37872000}}))
    (row,) = state.minute_flow.take_before(datetime(2026, 10, 19, 15, 0, tzinfo=UTC).timestamp())
    assert row.root == "SPXW" and row.symbol == "SPX"
    assert row.calls_delta_flow == pytest.approx(8 * 0.25)
    assert row.minute.astimezone(UTC) == datetime(2026, 10, 19, 14, 31, tzinfo=UTC)  # 37872000 ms = 10:31:12 ET


def test_the_recorder_is_off_unless_the_processor_enables_it() -> None:
    state = worker._ProcessorState(_FakeRelay(), _FakePrice())  # type: ignore[arg-type]
    state.minute_flow.set_deltas({CALL: 0.25})
    contract = {"security_type": "OPTION", "root": "SPXW", "expiration": 20261019, "strike": 7700000, "right": "C"}
    _handle_raw_frame(state, json.dumps({"header": {"type": "TRADE"}, "contract": contract, "trade": {"size": 8, "price": 10.5, "condition": 18}}))
    assert state.minute_flow.take_before(datetime(2030, 1, 1, tzinfo=UTC).timestamp()) == []


class _FakeRelay:
    def publish_quote(self, event) -> None:
        pass

    def publish_trade(self, event) -> None:
        pass


class _FakePrice:
    async def persist_if_due(self, event) -> None:
        pass


@pytest.mark.asyncio
async def test_the_in_memory_adapter_keeps_and_reads_the_minutes() -> None:
    adapter = SyncStorageAsyncReadAdapter(InMemoryStorage())
    row = MinuteOptionFlow(datetime(2026, 10, 19, 14, 31, tzinfo=UTC), "SPX", "SPXW", 1.5, -0.5, 2, 1, 14, 20)
    await adapter.save_minute_option_flow([row])
    assert await adapter.get_minute_option_flow("spx", datetime(2026, 10, 19, 14, 0, tzinfo=UTC), datetime(2026, 10, 19, 15, 0, tzinfo=UTC)) == [row]
    assert await adapter.get_minute_option_flow("SPX", datetime(2026, 10, 19, 15, 0, tzinfo=UTC), datetime(2026, 10, 19, 16, 0, tzinfo=UTC)) == []


@pytest.mark.asyncio
async def test_the_periodic_flush_writes_the_finished_minutes(monkeypatch: pytest.MonkeyPatch) -> None:
    state = worker._ProcessorState(_FakeRelay(), _FakePrice())  # type: ignore[arg-type]
    state.minute_flow_enabled = True
    state.minute_flow = _recorder()
    state.minute_flow.observe_trade(_trade(CALL, "10.50", size=2, at=datetime(2020, 1, 1, tzinfo=UTC)))  # an old minute: finished
    storage = AsyncMock()
    container = type("C", (), {"async_market_storage": storage})()
    monkeypatch.setattr(worker, "MINUTE_FLOW_FLUSH_DELAY_SECONDS", -60.0)  # fire the first flush immediately

    task = asyncio.create_task(worker._flush_minute_flow_periodically(container, state))
    for _ in range(50):
        await asyncio.sleep(0.02)
        if storage.save_minute_option_flow.await_count:
            break
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    storage.save_minute_option_flow.assert_awaited()
    (written,) = storage.save_minute_option_flow.await_args.args[0]
    assert written.contracts_total == 2
    assert state.minute_flow.take_before(datetime(2030, 1, 1, tzinfo=UTC).timestamp()) == []  # handed over, not kept


@pytest.mark.asyncio
async def test_a_failed_write_keeps_the_minutes_and_does_not_kill_the_task(monkeypatch: pytest.MonkeyPatch) -> None:
    state = worker._ProcessorState(_FakeRelay(), _FakePrice())  # type: ignore[arg-type]
    state.minute_flow_enabled = True
    state.minute_flow = _recorder()
    state.minute_flow.observe_trade(_trade(CALL, "10.50", size=2, at=datetime(2020, 1, 1, tzinfo=UTC)))
    storage = AsyncMock()
    storage.save_minute_option_flow.side_effect = RuntimeError("database down")
    container = type("C", (), {"async_market_storage": storage})()
    monkeypatch.setattr(worker, "MINUTE_FLOW_FLUSH_DELAY_SECONDS", -60.0)

    task = asyncio.create_task(worker._flush_minute_flow_periodically(container, state))
    for _ in range(50):
        await asyncio.sleep(0.02)
        if storage.save_minute_option_flow.await_count:
            break
    assert not task.done()  # the loop survived the failed write
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    (kept,) = state.minute_flow.take_before(datetime(2030, 1, 1, tzinfo=UTC).timestamp())
    assert kept.contracts_total == 2  # put back for the next flush


def test_the_endpoint_serves_the_minutes_with_the_share_of_classified_volume_and_validates_the_range() -> None:
    from fastapi.testclient import TestClient

    from backend.main import app

    with TestClient(app) as client:
        empty = client.get("/api/v1/flow/SPX/minutes")
        reversed_range = client.get("/api/v1/flow/SPX/minutes?start=2026-10-19T15:00:00Z&end=2026-10-19T14:00:00Z")
        too_long = client.get("/api/v1/flow/SPX/minutes?start=2026-10-01T00:00:00Z&end=2026-10-10T00:00:00Z")
        naive = client.get("/api/v1/flow/SPX/minutes?start=2026-10-19T14:00:00&end=2026-10-19T15:00:00")
        future = client.get("/api/v1/flow/NQ/minutes")

    assert empty.status_code == 200
    assert empty.json() == {"schema_version": 1, "symbol": "SPX", "minutes": []}
    assert reversed_range.status_code == 422 and too_long.status_code == 422 and naive.status_code == 422
    assert future.status_code == 200 and future.json()["symbol"] == "NQ"  # NQ reads NDX's flow


def test_the_serializer_rounds_and_computes_the_classified_share() -> None:
    from backend.api.serializers import minute_flow_response

    rows = [
        MinuteOptionFlow(datetime(2026, 10, 19, 14, 31, tzinfo=UTC), "SPX", "*", 12.3456, -7.891, 3, 1, 12, 20),
        MinuteOptionFlow(datetime(2026, 10, 19, 14, 32, tzinfo=UTC), "SPX", "*", 0.0, 0.0, 0, 0, 0, 0),
    ]
    payload = minute_flow_response("spx", rows)
    assert payload["symbol"] == "SPX"
    assert payload["minutes"][0] == {"minute": "2026-10-19T14:31:00Z", "calls_delta_flow": 12.35, "puts_delta_flow": -7.89, "classified_share": 0.6}
    assert payload["minutes"][1]["classified_share"] is None


def test_audit_is_off_by_default_and_keeps_nothing() -> None:
    recorder = _recorder()
    recorder.observe_trade(_trade(CALL, "10.50"))
    assert recorder.take_audit() == []


def test_audit_keeps_every_trade_inside_the_window_with_its_outcome() -> None:
    recorder = _recorder(delta=0.5)
    recorder.set_audit(frozenset({"SPX"}), "10:31-10:32")  # T0 is 10:31:10 ET
    recorder.observe_trade(_trade(CALL, "10.50", size=10))  # buy
    recorder.observe_trade(_trade(PUT, "10.00", size=4))  # sell a put: +2.0 of puts flow
    recorder.observe_trade(_trade(CALL, "10.20", size=3))  # inside the quote
    recorder.observe_trade(_trade(CALL, "10.50", size=2, condition=130))  # multi-leg
    recorder.observe_trade(_trade("SPY261019C00767000", "1.00", symbol="SPY"))  # other underlying: not audited
    rows = recorder.take_audit()
    assert [(r["occ"][-9], r["outcome"], r["contribution"]) for r in rows] == [("C", "buy", 5.0), ("P", "sell", 2.0), ("C", "inside_quote", 0.0), ("C", "other_condition", 0.0)]
    assert rows[0]["bid"] == 10.0 and rows[0]["ask"] == 10.5 and rows[0]["price"] == 10.5 and rows[0]["abs_delta"] == 0.5
    assert recorder.take_audit() == []


def test_audit_ignores_trades_outside_its_window_and_a_bad_window_stays_off() -> None:
    recorder = _recorder()
    recorder.set_audit(frozenset({"SPX"}), "10:32-10:35")
    recorder.observe_trade(_trade(CALL, "10.50"))  # 10:31 ET, before the window
    assert recorder.take_audit() == []
    broken = _recorder()
    broken.set_audit(frozenset({"SPX"}), "nonsense")
    broken.observe_trade(_trade(CALL, "10.50"))
    assert broken.take_audit() == []


def test_audit_file_gets_one_ndjson_line_per_row(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    worker._append_minute_flow_audit([{"occ": CALL, "outcome": "buy"}, {"occ": PUT, "outcome": "sell"}])
    (path,) = list((tmp_path / "logs").glob("minute_flow_audit_*.ndjson"))
    assert [json.loads(line)["outcome"] for line in path.read_text(encoding="utf-8").splitlines()] == ["buy", "sell"]
