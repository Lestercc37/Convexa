"""A picows-based connection to the Theta Terminal's local WebSocket.

Why: benchmarked on the real 2026-10-06 open capture (5.2M frames), the hub's
websockets reader tops out at ~67-73k frames/s per core while the open peaks at
~52k; picows (a Cython frame parser driven by a callback, no await per frame)
read 101k frames/s per core from a one-send-per-frame producer and 687k/s from
a batched one. The hub keeps exactly the same logic; this module only changes
how frames get from the socket into it.

The hub's own code only ever calls three things on its connection object --
`await send(text)`, `await recv()` (handshake phase only) and `await close()` --
which PicowsConnection provides. After the handshake phase the frames take the
callback path (`ThetaStreamHub._handle_picows_frame`), not `recv()`.

Opt-in: logs/ws_reader_picows.flag must exist when a connection opens (see
ThetaStreamHub._picows_enabled); delete it and reconnect to go back.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

try:  # optional dependency: without it the hub keeps using websockets
    import picows
except ImportError:  # pragma: no cover
    picows = None  # type: ignore[assignment]

if TYPE_CHECKING:
    from backend.adapters.providers.thetadata.provider import ThetaStreamHub

# Max payloads joined into one relay write when a read buffer holds many frames.
BATCH_MAX_FRAMES = 512


def picows_available() -> bool:
    return picows is not None


class PicowsConnection:
    """Duck-types the slice of websockets.ClientConnection the hub uses."""

    def __init__(self, hub: ThetaStreamHub) -> None:
        self.hub = hub
        self.transport = None
        # handshake phase: frames are delivered to recv() until `fast` is set
        self._handshake_frames: asyncio.Queue[str | None] = asyncio.Queue()
        self.fast = False
        self.closed = asyncio.Event()
        self.last_frame_at = time.monotonic()
        self._batch: list[bytes] = []

    # --- the API the hub relies on -------------------------------------
    async def send(self, text: str) -> None:
        if self.transport is None or self.closed.is_set():
            raise ConnectionError("Theta Terminal WebSocket is closed")
        self.transport.send(picows.WSMsgType.TEXT, text.encode("utf-8"))

    async def recv(self) -> str:
        frame = await self._handshake_frames.get()
        if frame is None:
            raise ConnectionError("Theta Terminal WebSocket closed")
        return frame

    async def close(self) -> None:
        if self.transport is not None:
            self.transport.disconnect()

    # --- the hot path ----------------------------------------------------
    def on_frame(self, frame) -> None:
        if frame.msg_type != picows.WSMsgType.TEXT:
            return
        self.last_frame_at = time.monotonic()
        payload = frame.get_payload_as_bytes()
        if not self.fast:
            self._handshake_frames.put_nowait(payload.decode("utf-8", "replace"))
            return
        hub = self.hub
        recorder = hub._frame_recorder
        if recorder is not None:
            recorder.record(payload.decode("utf-8", "replace"))
        if hub._handle_picows_frame(payload, self._batch):
            if frame.last_in_buffer or len(self._batch) >= BATCH_MAX_FRAMES:
                self.flush_batch()
        elif self._batch and frame.last_in_buffer:
            self.flush_batch()

    def flush_batch(self) -> None:
        if not self._batch:
            return
        batch, self._batch = self._batch, []
        self.hub._publish_picows_batch(batch)

    def on_disconnected(self) -> None:
        self.closed.set()
        self._handshake_frames.put_nowait(None)

    async def hold_until_closed(self, stale_after_seconds: float) -> None:
        """Replaces the recv() loop: returns only by raising, like that loop."""
        while True:
            try:
                await asyncio.wait_for(self.closed.wait(), timeout=1.0)
            except TimeoutError:
                if time.monotonic() - self.last_frame_at > stale_after_seconds:
                    raise ConnectionError("No message from Theta Terminal within heartbeat window") from None
                continue
            raise ConnectionError("Theta Terminal WebSocket closed")


if picows is not None:

    class _Listener(picows.WSListener):
        def __init__(self, connection: PicowsConnection) -> None:
            super().__init__()
            self._connection = connection

        def on_ws_connected(self, transport) -> None:
            self._connection.transport = transport

        def on_ws_frame(self, transport, frame) -> None:
            self._connection.on_frame(frame)

        def on_ws_disconnected(self, transport) -> None:
            self._connection.on_disconnected()


async def connect(hub: ThetaStreamHub, url: str) -> PicowsConnection:
    """Opens the connection; the caller owns closing it."""
    if picows is None:
        raise RuntimeError("picows is not installed")
    connection = PicowsConnection(hub)
    transport, _listener = await picows.ws_connect(
        lambda: _Listener(connection),
        url,
        enable_auto_ping=False,  # the Terminal pushes a STATUS every second; the hub's own heartbeat check covers staleness
        websocket_handshake_timeout=5,
    )
    connection.transport = transport
    return connection
