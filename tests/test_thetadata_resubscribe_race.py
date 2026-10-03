"""Integration tests for ThetaStreamHub against a fake Theta Terminal that
speaks the real WebSocket protocol over a real local socket -- STATUS
frames every few ms, STREAM-add requests answered with REQ_RESPONSE -- and
that reproduces the 2026-10-03 04:25:33 failure: while its upstream (FPSS)
link is down, every subscribe request is answered ERROR (the real
Terminal's NullPointerException on its not-yet-reconnected link), while the
local WebSocket itself accepts connections the whole time.

Production constants (2s reconnect delay, 5s verify window...) are scaled
down so the whole race plays out in well under a second."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator
from datetime import date
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio
from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from backend.adapters.providers.thetadata import provider as provider_module
from backend.adapters.providers.thetadata.provider import ThetaStreamHub
from backend.domain.entities import ContractType, UnderlyingKind

CONTRACT_COUNT = 6
# 6 contracts x (TRADE + QUOTE) + 1 underlying
EXPECTED_SUBSCRIPTIONS = CONTRACT_COUNT * 2 + 1


class FakeTerminal:
    def __init__(self) -> None:
        self.fpss = "up"  # "up" | "down"
        self.reject_budget = 0  # reject this many requests even while "up"
        self.reject_everything = False
        # Real cold-start behaviour (measured 2026-10-03): until the first
        # STREAM add arrives the Terminal reports DISCONNECTED and has not
        # logged in to ThetaData; the add triggers the login, is held until
        # it completes, then answered SUBSCRIBED.
        self.lazy_fpss_login = False
        self.lazy_login_delay = 0.2
        self.connections = 0
        self.subscribe_requests = 0
        self.rejected = 0
        self.accepted_by_connection: dict[int, set[str]] = {}
        self._server = None
        self.url = ""

    async def start(self) -> None:
        self._server = await serve(self._handler, "127.0.0.1", 0)
        port = self._server.sockets[0].getsockname()[1]
        self.url = f"ws://127.0.0.1:{port}/v1/events"

    async def stop(self) -> None:
        assert self._server is not None
        self._server.close()
        await self._server.wait_closed()

    async def drop_fpss(self, down_for: float) -> None:
        """Upstream link goes down now and comes back after `down_for`."""
        self.fpss = "down"
        await asyncio.sleep(down_for)
        self.fpss = "up"

    @property
    def latest_accepted(self) -> int:
        if not self.accepted_by_connection:
            return 0
        return len(self.accepted_by_connection[max(self.accepted_by_connection)])

    async def _status_loop(self, websocket: ServerConnection) -> None:
        while True:
            status = "CONNECTED" if self.fpss == "up" else "DISCONNECTED"
            await websocket.send(json.dumps({"header": {"type": "STATUS", "status": status}}))
            await asyncio.sleep(0.02)

    async def _handler(self, websocket: ServerConnection) -> None:
        self.connections += 1
        connection_id = self.connections
        self.accepted_by_connection[connection_id] = set()
        status_task = asyncio.create_task(self._status_loop(websocket))
        with contextlib.suppress(ConnectionClosed):
            async for raw in websocket:
                message = json.loads(raw)
                if message.get("msg_type") != "STREAM":
                    continue
                self.subscribe_requests += 1
                if self.lazy_fpss_login and self.fpss == "down":
                    await asyncio.sleep(self.lazy_login_delay)
                    self.fpss = "up"
                accepted = self.fpss == "up" and not self.reject_everything
                if accepted and self.reject_budget > 0:
                    self.reject_budget -= 1
                    accepted = False
                if accepted:
                    key = f'{message["sec_type"]}|{message["req_type"]}|{json.dumps(message["contract"], sort_keys=True)}'
                    self.accepted_by_connection[connection_id].add(key)
                else:
                    self.rejected += 1
                await websocket.send(
                    json.dumps(
                        {
                            "header": {
                                "type": "REQ_RESPONSE",
                                "status": "CONNECTED",
                                "response": "SUBSCRIBED" if accepted else "ERROR",
                                "req_id": message["id"],
                            }
                        }
                    )
                )
        status_task.cancel()


@pytest_asyncio.fixture
async def terminal() -> AsyncIterator[FakeTerminal]:
    fake = FakeTerminal()
    await fake.start()
    try:
        yield fake
    finally:
        await fake.stop()


@pytest.fixture(autouse=True)
def fast_timings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(provider_module, "RECONNECT_BASE_DELAY_SECONDS", 0.1)
    # raising=False so the race test can also be pointed at a provider.py
    # from before these constants existed, to show it fails there.
    monkeypatch.setattr(provider_module, "SUBSCRIPTION_VERIFY_WAIT_SECONDS", 0.3, raising=False)
    monkeypatch.setattr(
        provider_module, "SUBSCRIPTION_RETRY_DELAYS_SECONDS", (0.1, 0.1, 0.1), raising=False
    )
    monkeypatch.setattr(provider_module, "TERMINAL_CONNECTED_WAIT_SECONDS", 3, raising=False)


def _hub(url: str) -> ThetaStreamHub:
    hub = ThetaStreamHub(url, httpx.Client(base_url="http://127.0.0.1:1"))
    for index in range(CONTRACT_COUNT):
        hub.register_contract(
            f"SPY261016C0077{index}000",
            "SPY",
            date(2026, 10, 16),
            ContractType.CALL,
            Decimal(770 + index),
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


class TestResubscribeRaceAgainstFakeTerminal:
    @pytest.mark.asyncio
    async def test_resubscribe_waits_for_the_terminals_upstream_link_instead_of_hitting_the_gap(
        self, terminal: FakeTerminal
    ) -> None:
        """The exact race of 2026-10-03 04:25:33: the upstream link drops,
        the hub reconnects its local WebSocket 0.1s later (well inside the
        0.35s the link stays down) and used to subscribe at once -- every
        request answered ERROR, nothing retried. It must wait for the
        Terminal to report CONNECTED, so the Terminal never has to reject
        anything."""
        hub = _hub(terminal.url)
        hub.start()
        try:
            assert await _until(lambda: terminal.latest_accepted == EXPECTED_SUBSCRIPTIONS)
            assert terminal.connections == 1

            await terminal.drop_fpss(down_for=0.35)

            assert await _until(
                lambda: terminal.connections >= 2
                and terminal.latest_accepted == EXPECTED_SUBSCRIPTIONS
            )
            assert terminal.rejected == 0, (
                "subscribe requests reached the Terminal while its upstream link was down"
            )
        finally:
            await hub.stop()

    @pytest.mark.asyncio
    async def test_a_freshly_started_terminal_that_only_logs_in_on_first_subscribe_is_not_deadlocked(
        self, terminal: FakeTerminal, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression for the first version of the wait (deployed 2026-10-03
        12:13, found 12:51): a cold-started Terminal stays DISCONNECTED until
        its first STREAM add, so waiting for CONNECTED forever never
        subscribed and the worker looped in backoff. The wait must give up
        after its grace period and subscribe."""
        monkeypatch.setattr(provider_module, "TERMINAL_CONNECTED_WAIT_SECONDS", 0.5)
        terminal.fpss = "down"
        terminal.lazy_fpss_login = True
        hub = _hub(terminal.url)
        hub.start()
        try:
            assert await _until(lambda: terminal.latest_accepted == EXPECTED_SUBSCRIPTIONS)
            assert terminal.rejected == 0
            # One extra reconnect is the pre-existing cold-start behaviour
            # (the Terminal keeps reporting DISCONNECTED until its login
            # completes, which the hub treats as a reason to reconnect once);
            # what must not happen is never subscribing.
            assert terminal.connections <= 2
        finally:
            await hub.stop()

    @pytest.mark.asyncio
    async def test_the_wait_is_short_when_the_terminal_reports_connected_at_once(
        self, terminal: FakeTerminal, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(provider_module, "TERMINAL_CONNECTED_WAIT_SECONDS", 30)
        hub = _hub(terminal.url)
        hub.start()
        try:
            assert await _until(lambda: terminal.latest_accepted == EXPECTED_SUBSCRIPTIONS, timeout=3)
        finally:
            await hub.stop()

    @pytest.mark.asyncio
    async def test_a_status_frame_left_over_from_the_dead_connection_does_not_force_another_reconnect(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        hub = ThetaStreamHub("ws://unused", httpx.Client(base_url="http://127.0.0.1:1"))
        reconnects: list[int] = []
        monkeypatch.setattr(hub, "request_reconnect", lambda: reconnects.append(1))
        disconnected = json.dumps({"header": {"type": "STATUS", "status": "DISCONNECTED"}})
        # Two DISCONNECTED frames were enqueued by the old connection; the
        # new connection became ready after them.
        hub._message_queue.put_nowait(disconnected)
        hub._message_queue.put_nowait(disconnected)
        hub._frames_enqueued = 2
        hub._stale_frames_before = 2
        # ...and one DISCONNECTED frame that really belongs to the new one.
        hub._message_queue.put_nowait(disconnected)
        hub._frames_enqueued = 3

        task = asyncio.create_task(hub._process_messages())
        await _until(lambda: hub._message_queue.empty())
        await asyncio.sleep(0.05)
        task.cancel()

        assert reconnects == [1]


class TestSubscriptionSafetyNet:
    @pytest.mark.asyncio
    async def test_rejections_despite_a_connected_status_are_retried_without_reconnecting(
        self, terminal: FakeTerminal
    ) -> None:
        """The case the wait can't prevent: the Terminal says CONNECTED yet
        still rejects some requests (here 5 of the 13). The hub must count
        them and re-send exactly those."""
        terminal.reject_budget = 5
        hub = _hub(terminal.url)
        hub.start()
        try:
            assert await _until(lambda: terminal.latest_accepted == EXPECTED_SUBSCRIPTIONS)
            assert terminal.rejected == 5
            assert terminal.connections == 1
            assert terminal.subscribe_requests == EXPECTED_SUBSCRIPTIONS + 5
        finally:
            await hub.stop()

    @pytest.mark.asyncio
    async def test_a_persistent_rejection_escalates_to_a_reconnect_but_never_storms(
        self, terminal: FakeTerminal, caplog: pytest.LogCaptureFixture
    ) -> None:
        terminal.reject_everything = True
        hub = _hub(terminal.url)
        with caplog.at_level(logging.CRITICAL):
            hub.start()
            try:
                assert await _until(
                    lambda: any("not reconnecting again" in r.message for r in caplog.records),
                    timeout=10,
                )
                connections_at_cap = terminal.connections
                await asyncio.sleep(1.5)  # long enough for any further escalation to show
            finally:
                await hub.stop()

        # 3 verifications in a row ended in rejection: connection 1 -> forced
        # reconnect -> connection 2 -> forced reconnect -> connection 3 ->
        # cap reached, stays put.
        assert connections_at_cap == 3
        assert terminal.connections == 3

    @pytest.mark.asyncio
    async def test_only_the_transient_error_response_is_retried(
        self, terminal: FakeTerminal
    ) -> None:
        hub = _hub(terminal.url)
        hub._subscription_requests[1] = ("option", ("SPY", date(2026, 10, 16), ContractType.CALL, Decimal(770), "TRADE"))
        hub._subscription_requests[2] = ("option", ("SPY", date(2026, 10, 16), ContractType.CALL, Decimal(771), "TRADE"))
        hub._note_subscription_response({"header": {"req_id": 1, "response": "MAX_STREAMS_REACHED"}})
        hub._note_subscription_response({"header": {"req_id": 2, "response": "ERROR"}})
        assert [args[3] for _, args in hub._failed_subscriptions] == [Decimal(771)]
