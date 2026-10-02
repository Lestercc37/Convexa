from __future__ import annotations

import asyncio
import contextlib
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
    ) -> None:
        self.server = server
        self.client = client
        self.received_frames = received_frames


@pytest_asyncio.fixture
async def relay_pair() -> AsyncIterator[_RelayPair]:
    server = StreamProcessorRelayServer("127.0.0.1", 0)
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
        yield _RelayPair(server, client, received_frames)
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


class TestNoReturnChannel:
    """v2 (2026-10-02): v1's relay sent a classified result back to
    worker.py for every raw frame and collapsed under real volume (see
    docs/stream-processor-split-postmortem-2026-10-02.md) -- this module
    now has no return channel at all, not merely one nobody happens to
    use. These tests fail loudly (AttributeError) if that return path is
    ever reintroduced without a conscious decision to revisit it."""

    def test_relay_client_has_no_method_to_publish_anything_back(self) -> None:
        client = StreamProcessorRelayClient("127.0.0.1", 0)
        assert not hasattr(client, "publish_event")
        assert not hasattr(client, "_outbound")

    def test_relay_server_takes_no_on_event_callback(self) -> None:
        # Constructing with only host/port (no callback parameter) is
        # itself the test -- a reintroduced on_event parameter would
        # make this call fail as a TypeError... but since it would be
        # optional-with-default in that scenario too, assert the
        # signature directly instead of relying on that.
        import inspect

        params = inspect.signature(StreamProcessorRelayServer.__init__).parameters
        assert set(params) == {"self", "host", "port"}

    @pytest.mark.asyncio
    async def test_sending_arbitrary_data_from_a_raw_client_socket_has_no_observable_effect(
        self,
    ) -> None:
        # _handle_client only ever awaits reader.read() for EOF -- proven
        # here with a raw socket (bypassing StreamProcessorRelayClient,
        # which per the test above has no send capability at all): send
        # well-formed-looking JSON lines, as if this WERE a return
        # channel, and confirm the server does nothing observable with
        # them -- no exception, publish_raw() still works normally
        # afterward for a real client.
        server = StreamProcessorRelayServer("127.0.0.1", 0)
        await server.start()
        try:
            raw_reader, raw_writer = await asyncio.open_connection("127.0.0.1", server.port)
            try:
                raw_writer.write(b'{"k": "quote", "symbol": "SPY"}\n' * 100)
                await raw_writer.drain()
                await asyncio.sleep(0.05)  # give the server a moment to misbehave, if it would

                received_frames: list[str] = []
                client = StreamProcessorRelayClient("127.0.0.1", server.port)
                client_task = asyncio.create_task(
                    client.run(on_raw_frame=received_frames.append)
                )
                try:
                    # Two connections now: the raw manual one above, plus
                    # this real client -- has_client alone would already
                    # be True from the raw connection, racing ahead of
                    # the real client actually being ready to receive.
                    await _wait_until(lambda: len(server._clients) >= 2)
                    frame = '{"header":{"type":"QUOTE"}}'
                    server.publish_raw(frame)
                    await _wait_until(lambda: len(received_frames) == 1)
                    assert received_frames[0] == frame
                finally:
                    client_task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await client_task
            finally:
                raw_writer.close()
                with contextlib.suppress(Exception):
                    await raw_writer.wait_closed()
        finally:
            await server.stop()


class TestBackpressure:
    def test_publish_raw_with_no_client_connected_returns_false(self) -> None:
        server = StreamProcessorRelayServer("127.0.0.1", 0)
        assert server.publish_raw('{"header":{}}') is False

    def test_has_client_reflects_connection_state(self) -> None:
        server = StreamProcessorRelayServer("127.0.0.1", 0)
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


def test_reconnect_backoff_constants_match_the_rest_of_the_codebase() -> None:
    # Deliberately the same numbers as whale_alerts_relay.py/ThetaStreamHub
    # -- not asserting a specific value in isolation, just that nobody
    # quietly drifted this module away from the shared convention.
    assert RECONNECT_BASE_DELAY_SECONDS == 2
    assert RELAY_QUEUE_MAXSIZE > 0
