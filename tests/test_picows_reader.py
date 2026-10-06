"""The opt-in picows reader (logs/ws_reader_picows.flag) against a fake Terminal that speaks the
real WebSocket protocol over a real local socket: same subscribe/confirm behaviour as the
websockets reader, plus the callback fast path (batched relay writes, OHLC dropped, QUOTE/TRADE
timestamps, in-process fallback when no processor is connected)."""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from backend.adapters.providers.thetadata import picows_connection
from backend.adapters.providers.thetadata import provider as provider_module
from backend.adapters.providers.thetadata.provider import ThetaStreamHub
from backend.domain.entities import ContractType, UnderlyingKind

pytestmark = pytest.mark.skipif(not picows_connection.picows_available(), reason="picows is not installed")

QUOTE = (
    '{"header":{"type":"QUOTE","status":"CONNECTED"},"contract":{"security_type":"OPTION","root":"SPY",'
    '"expiration":20261016,"strike":770000,"right":"C"},"quote":{"bid":1.08,"ask":1.09,"date":20261006,"ms_of_day":34200000}}'
)
TRADE = (
    '{"header":{"type":"TRADE","status":"CONNECTED"},"contract":{"security_type":"OPTION","root":"SPY",'
    '"expiration":20261016,"strike":770000,"right":"C"},"trade":{"size":3,"price":1.09,"date":20261006,"ms_of_day":34200001}}'
)
UNDERLYING_TRADE = (
    '{"header":{"type":"TRADE","status":"CONNECTED"},"contract":{"security_type":"STOCK","root":"SPY"},'
    '"trade":{"size":100,"price":770.5,"date":20261006,"ms_of_day":34200002}}'
)
OHLC = (
    '{"header":{"type":"OHLC","status":"CONNECTED"},"contract":{"security_type":"STOCK","root":"SPY"},'
    '"ohlc":{"open":1.0,"high":2.0,"low":0.5,"close":1.5,"volume":10}}'
)


class FakeTerminal:
    def __init__(self) -> None:
        self.connections = 0
        self.confirmed = 0
        self.url = ""
        self._server = None
        self._sockets: list[ServerConnection] = []

    async def start(self) -> None:
        self._server = await serve(self._handler, "127.0.0.1", 0)
        port = self._server.sockets[0].getsockname()[1]
        self.url = f"ws://127.0.0.1:{port}/v1/events"

    async def stop(self) -> None:
        self._server.close()
        await self._server.wait_closed()

    async def push(self, frames: list[str]) -> None:
        for websocket in self._sockets:
            for frame in frames:
                await websocket.send(frame)

    async def close_clients(self) -> None:
        for websocket in list(self._sockets):
            await websocket.close()

    async def _status_loop(self, websocket: ServerConnection) -> None:
        while True:
            await websocket.send(json.dumps({"header": {"type": "STATUS", "status": "CONNECTED"}}))
            await asyncio.sleep(0.02)

    async def _handler(self, websocket: ServerConnection) -> None:
        self.connections += 1
        self._sockets.append(websocket)
        status_task = asyncio.create_task(self._status_loop(websocket))
        with contextlib.suppress(ConnectionClosed):
            async for raw in websocket:
                message = json.loads(raw)
                if message.get("msg_type") != "STREAM":
                    continue
                self.confirmed += 1
                await websocket.send(
                    json.dumps(
                        {
                            "header": {
                                "type": "REQ_RESPONSE",
                                "status": "CONNECTED",
                                "response": "SUBSCRIBED",
                                "req_id": message["id"],
                            }
                        }
                    )
                )
        status_task.cancel()
        self._sockets.remove(websocket)


class StubRelay:
    def __init__(self, has_client: bool = True) -> None:
        self.has_client = has_client
        self.chunks: list[bytes] = []

    def publish_bytes(self, data: bytes) -> bool:
        if not self.has_client:
            return False
        self.chunks.append(data)
        return True

    def publish_raw(self, raw: str) -> bool:
        if not self.has_client:
            return False  # like the real relay: nothing connected, the caller handles it in-process
        raise AssertionError("the picows reader must publish bytes")  # pragma: no cover

    @property
    def lines(self) -> list[bytes]:
        return [line for chunk in self.chunks for line in chunk.split(bytes([10])) if line]


@pytest_asyncio.fixture
async def terminal() -> AsyncIterator[FakeTerminal]:
    fake = FakeTerminal()
    await fake.start()
    try:
        yield fake
    finally:
        await fake.stop()


@pytest.fixture(autouse=True)
def picows_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "ws_reader_picows.flag").write_text("on")
    monkeypatch.setattr(provider_module, "RECONNECT_BASE_DELAY_SECONDS", 0.1)
    monkeypatch.setattr(provider_module, "SUBSCRIPTION_VERIFY_WAIT_SECONDS", 0.3, raising=False)
    monkeypatch.setattr(provider_module, "TERMINAL_CONNECTED_WAIT_SECONDS", 3, raising=False)


def _hub(url: str) -> ThetaStreamHub:
    hub = ThetaStreamHub(url, httpx.Client(base_url="http://127.0.0.1:1"))
    for index in range(4):
        hub.register_contract(
            f"SPY261016C0077{index}000", "SPY", date(2026, 10, 16), ContractType.CALL, Decimal(770 + index)
        )
    hub.register_symbol("SPY", UnderlyingKind.EQUITY)
    return hub


async def _until(predicate, timeout: float = 6.0) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return False


EXPECTED = 4 * 2 + 1


class TestPicowsReader:
    @pytest.mark.asyncio
    async def test_it_connects_subscribes_and_gets_every_subscription_confirmed(self, terminal: FakeTerminal) -> None:
        hub = _hub(terminal.url)
        hub.start()
        try:
            assert await _until(lambda: terminal.confirmed == EXPECTED)
            assert isinstance(hub._active_websocket, picows_connection.PicowsConnection)
            assert await _until(lambda: not hub._failed_subscriptions and hub._terminal_connected)
        finally:
            await hub.stop()

    @pytest.mark.asyncio
    async def test_quotes_and_trades_reach_the_processor_relay_in_batches_and_ohlc_is_dropped(
        self, terminal: FakeTerminal
    ) -> None:
        hub = _hub(terminal.url)
        relay = StubRelay()
        hub.set_processor_relay(relay)
        hub.start()
        try:
            assert await _until(lambda: terminal.confirmed == EXPECTED)
            await terminal.push([QUOTE] * 50 + [TRADE] + [UNDERLYING_TRADE] + [OHLC] * 10)
            assert await _until(lambda: len(relay.lines) == 52)
            assert OHLC.encode() not in relay.lines
            assert relay.lines.count(QUOTE.encode()) == 50
            assert hub._last_quote_at is not None
            assert hub._last_option_trade_at is not None
            assert hub._last_underlying_trade_at is not None
            assert len(relay.chunks) < 52, "frames of one read buffer are written as one chunk"
        finally:
            await hub.stop()

    @pytest.mark.asyncio
    async def test_without_a_processor_the_frames_are_handled_in_process(self, terminal: FakeTerminal) -> None:
        hub = _hub(terminal.url)
        queue = hub.subscribe_quote_queue("SPY")
        hub.start()
        try:
            assert await _until(lambda: terminal.confirmed == EXPECTED)
            await terminal.push([QUOTE])
            event = await asyncio.wait_for(queue.get(), timeout=3)
            assert event.bid == Decimal("1.08")
        finally:
            await hub.stop()

    @pytest.mark.asyncio
    async def test_a_batch_published_after_the_processor_left_falls_back_to_in_process(
        self, terminal: FakeTerminal
    ) -> None:
        hub = _hub(terminal.url)
        queue = hub.subscribe_quote_queue("SPY")
        hub.set_processor_relay(StubRelay(has_client=False))
        hub.start()
        try:
            assert await _until(lambda: hub._message_queue.maxsize > 0)
            hub._publish_picows_batch([QUOTE.encode()])
            event = await asyncio.wait_for(queue.get(), timeout=3)
            assert event.ask == Decimal("1.09")
        finally:
            await hub.stop()

    @pytest.mark.asyncio
    async def test_it_reconnects_and_resubscribes_after_the_terminal_closes_the_socket(
        self, terminal: FakeTerminal
    ) -> None:
        hub = _hub(terminal.url)
        hub.start()
        try:
            assert await _until(lambda: terminal.confirmed == EXPECTED)
            await terminal.close_clients()
            assert await _until(lambda: terminal.connections >= 2 and terminal.confirmed == 2 * EXPECTED)
        finally:
            await hub.stop()

    @pytest.mark.asyncio
    async def test_without_the_flag_the_websockets_reader_is_used(
        self, terminal: FakeTerminal, tmp_path: Path
    ) -> None:
        (tmp_path / "logs" / "ws_reader_picows.flag").unlink()
        hub = _hub(terminal.url)
        hub.start()
        try:
            assert await _until(lambda: terminal.confirmed == EXPECTED)
            assert not isinstance(hub._active_websocket, picows_connection.PicowsConnection)
        finally:
            await hub.stop()
