from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from backend.core.stream_processor_relay import (
    RECONNECT_BASE_DELAY_SECONDS,
    RELAY_QUEUE_MAXSIZE,
    StreamProcessorRelayClient,
    StreamProcessorRelayServer,
    _ConnectedProcessor,
)


class _RelayPair:
    def __init__(
        self,
        server: StreamProcessorRelayServer,
        client: StreamProcessorRelayClient,
        received_frames: list[str],
        received_events: list[dict],
    ) -> None:
        self.server = server
        self.client = client
        self.received_frames = received_frames
        self.received_events = received_events


@pytest_asyncio.fixture
async def relay_pair() -> AsyncIterator[_RelayPair]:
    received_events: list[dict] = []
    server = StreamProcessorRelayServer("127.0.0.1", 0, on_event=received_events.append)
    await server.start()
    client = StreamProcessorRelayClient("127.0.0.1", server.port)
    received_frames: list[str] = []
    client_task = asyncio.create_task(client.run(on_raw_frame=received_frames.append))
    # Same pattern as test_whale_alerts_relay.py's own relay_pair fixture
    # -- give the client's connect task a moment to actually establish.
    for _ in range(50):
        if server.has_client:
            break
        await asyncio.sleep(0.02)
    try:
        yield _RelayPair(server, client, received_frames, received_events)
    finally:
        client_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await client_task
        await server.stop()


async def _wait_until(predicate, timeout: float = 2.0, interval: float = 0.02) -> None:
    async def _poll() -> None:
        while not predicate():
            await asyncio.sleep(interval)

    await asyncio.wait_for(_poll(), timeout=timeout)


class TestRoundTrip:
    @pytest.mark.asyncio
    async def test_a_published_raw_frame_is_received_intact_on_the_other_end(
        self, relay_pair: _RelayPair
    ) -> None:
        frame = '{"header":{"type":"QUOTE"},"contract":{"root":"SPY"}}'

        sent = relay_pair.server.publish_raw(frame)
        await _wait_until(lambda: len(relay_pair.received_frames) == 1)

        assert sent is True
        assert relay_pair.received_frames[0] == frame

    @pytest.mark.asyncio
    async def test_a_published_classified_event_is_received_intact_on_the_other_end(
        self, relay_pair: _RelayPair
    ) -> None:
        payload = {"k": "quote", "symbol": "SPY", "occ_symbol": "SPY260101C00500000"}

        relay_pair.client.publish_event(payload)
        await _wait_until(lambda: len(relay_pair.received_events) == 1)

        assert relay_pair.received_events[0] == payload

    @pytest.mark.asyncio
    async def test_a_large_burst_of_raw_frames_arrives_without_drops_or_reordering(
        self, relay_pair: _RelayPair
    ) -> None:
        # Same regression this project already proved it needed for
        # whale_alerts_relay.py, 2026-09-24 (drain-per-message overhead
        # alone dropped 2M+ messages under real volume) -- this relay
        # uses the identical batch-drain-before-single-drain() shape, so
        # this is the matching regression test for it.
        count = 5000
        frames = [f'{{"header":{{"type":"QUOTE"}},"seq":{i}}}' for i in range(count)]
        for frame in frames:
            relay_pair.server.publish_raw(frame)

        await _wait_until(lambda: len(relay_pair.received_frames) >= count, timeout=15)

        assert relay_pair.received_frames == frames

    @pytest.mark.asyncio
    async def test_a_large_burst_of_classified_events_arrives_without_drops_or_reordering(
        self, relay_pair: _RelayPair
    ) -> None:
        count = 5000
        payloads = [{"k": "quote", "seq": i} for i in range(count)]
        for payload in payloads:
            relay_pair.client.publish_event(payload)

        await _wait_until(lambda: len(relay_pair.received_events) >= count, timeout=15)

        assert relay_pair.received_events == payloads


class TestBackpressure:
    def test_publish_raw_with_no_client_connected_returns_false(self) -> None:
        server = StreamProcessorRelayServer("127.0.0.1", 0, on_event=lambda _payload: None)
        assert server.publish_raw('{"header":{}}') is False

    def test_has_client_reflects_connection_state(self) -> None:
        server = StreamProcessorRelayServer("127.0.0.1", 0, on_event=lambda _payload: None)
        assert server.has_client is False

    @pytest.mark.asyncio
    async def test_a_full_server_side_outbound_queue_drops_and_logs_critical(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        class _FakeWriter:
            def write(self, _data: bytes) -> None:
                pass

            async def drain(self) -> None:
                pass

            def close(self) -> None:
                pass

            async def wait_closed(self) -> None:
                pass

        connected = _ConnectedProcessor(_FakeWriter())  # type: ignore[arg-type]
        connected.send_task.cancel()  # stop the drain task so the queue actually fills
        for i in range(RELAY_QUEUE_MAXSIZE):
            connected.queue.put_nowait(f"{i}\n".encode())

        with caplog.at_level(logging.CRITICAL):
            connected.publish(b"one more\n")  # must not raise

        assert any(
            "outbound (raw frame) queue full" in record.message for record in caplog.records
        )
        await connected.writer.wait_closed()  # no-op on the fake, keeps cleanup symmetric

    @pytest.mark.asyncio
    async def test_a_full_client_side_outbound_queue_drops_and_logs_critical(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        client = StreamProcessorRelayClient("127.0.0.1", 0)
        for i in range(RELAY_QUEUE_MAXSIZE):
            client._outbound.put_nowait(f"{i}\n".encode())

        with caplog.at_level(logging.CRITICAL):
            client.publish_event({"k": "quote"})  # must not raise

        assert any(
            "outbound (classified event) queue full" in record.message
            for record in caplog.records
        )


def test_reconnect_backoff_constants_match_the_rest_of_the_codebase() -> None:
    # Deliberately the same numbers as whale_alerts_relay.py/ThetaStreamHub
    # -- not asserting a specific value in isolation, just that nobody
    # quietly drifted this module away from the shared convention.
    assert RECONNECT_BASE_DELAY_SECONDS == 2
    assert RELAY_QUEUE_MAXSIZE > 0
