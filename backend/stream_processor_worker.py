"""Standalone entrypoint for QUOTE/TRADE parsing and classification --
the CPU-bound work that used to run inline in backend/worker.py's own
ThetaStreamHub._process_messages, as a separate asyncio.Task on the same
event loop (PR #219, 2026-10-01).

Split out 2026-10-02: PR #219 proved that separating the WS read loop
(_consume) from parsing/dispatch (_process_messages) into two asyncio
Tasks stopped the read loop from being directly blocked by processing --
confirmed live, zero SLOW CONSUMER disconnects of that specific kind
since. It did NOT, and architecturally could not, give the two genuine
parallelism: both tasks still run on the same event loop, same OS
thread, same process, cooperatively interleaved at await points only.
Confirmed live, 2026-10-02 (message-lag diagnostic logging, added the
same day): _process_messages' own per-message work measured 100-150ms
on individual iterations under real market-open volume (~17,000-23,000
msgs/sec) -- during that whole synchronous span the event loop cannot
run _consume()'s own pending recv() either, enough to build a backlog of
2,000-3,000+ messages at that rate. Matches Theta Terminal's own
independent "SLOW CONSUMER: N packets dropped" log for the same
incidents (1,062,092 dropped packets, 2026-10-02 9:50-11:55am ET) and
Eduardo/ThetaData support's own read of it: the GIL, not the two-task
split, was the real ceiling. Only a separate OS process -- its own
interpreter, its own GIL -- removes it.

This process never opens a connection to ThetaData itself -- Theta
Terminal allows exactly one WebSocket connection account-wide (see
ThetaStreamHub's own docstring), already held by backend/worker.py.
Instead it connects to that process's local-only relay
(StreamProcessorRelayServer, backend/core/stream_processor_relay.py)
over a plain TCP socket on this same machine: receives raw WS frames,
parses/classifies them with the exact same pure logic
ThetaStreamHub._handle_quote/_handle_option_trade/
_handle_underlying_trade call when running in-process (see
backend/adapters/providers/thetadata/stream_parsing.py -- literally the
same functions, not a reimplementation), and sends the classified
result back over the same connection for worker.py to finish
dispatching (cumulative volume, message-lag accounting, the actual
subscriber-queue fan-out).

Deliberately stateless beyond ACTIVE_UNDERLYINGS (a static, shared
constant both processes import independently -- see
parse_underlying_trade_message's own `symbols` parameter): no DB, no
storage engine, no Container at all. This process does one thing --
pure, in-memory message transformation -- and needs nothing else to do
it, which is also what makes it safe to restart at any time: it carries
no state across a restart that worker.py doesn't already have its own
copy of.

If this process is down, worker.py's own StreamProcessorRelayServer.
publish_raw() just returns False (no client connected) and
_process_messages falls back to handling QUOTE/TRADE itself, in-process
-- today's known-working (if GIL-contended) behavior, never silently
dropped market data. See StreamProcessorRelayServer's own docstring.

Run with:
    python -m backend.stream_processor_worker

Also run backend/worker.py (as its own separate process) -- without it,
this process has no relay server to connect to, and never receives a
single raw frame.
"""

from __future__ import annotations

import asyncio
import json
import logging

from backend.adapters.providers.thetadata.stream_parsing import (
    encode_parsed_event,
    parse_option_trade_message,
    parse_quote_message,
    parse_underlying_trade_message,
)
from backend.core.logging import configure_logging
from backend.core.settings import get_settings
from backend.core.stream_processor_relay import StreamProcessorRelayClient
from backend.domain.underlyings import ACTIVE_UNDERLYINGS

logger = logging.getLogger(__name__)

_SYMBOLS = {underlying.symbol: underlying.kind for underlying in ACTIVE_UNDERLYINGS}


def _handle_raw_frame(relay: StreamProcessorRelayClient, raw: str) -> None:
    """Parses one raw WS frame and, if it produced a classified result,
    hands it straight back to the relay client to send to worker.py.
    Synchronous and exception-isolated per frame -- a single malformed
    message must never take down this process's whole relay connection,
    same principle as StreamProcessorRelayServer's own reader loop."""
    try:
        message = json.loads(raw)
        header = message.get("header", {})
        msg_type = header.get("type")
        if msg_type == "QUOTE":
            parsed = parse_quote_message(message)
        elif msg_type == "TRADE":
            security_type = message.get("contract", {}).get("security_type")
            if security_type == "OPTION":
                parsed = parse_option_trade_message(message)
            elif security_type in ("STOCK", "INDEX"):
                parsed = parse_underlying_trade_message(message, _SYMBOLS)
            else:
                return
        else:
            return
        if parsed is not None:
            relay.publish_event(encode_parsed_event(parsed))
    except Exception:
        logger.exception("Stream processor worker: failed to parse one raw frame, dropping it")


async def run() -> None:
    settings = get_settings()
    configure_logging(settings, log_file="logs/stream_processor_worker.log")
    logger.info("Starting %s stream processor worker", settings.app_name)

    if not settings.enable_scheduler:
        # Same kill switch backend/worker.py's own process checks.
        logger.warning(
            "enable_scheduler is False -- stream processor worker has nothing to "
            "start, exiting"
        )
        return

    relay = StreamProcessorRelayClient(
        settings.stream_processor_relay_host, settings.stream_processor_relay_port
    )
    logger.info(
        "Stream processor worker running, connecting to relay at %s:%s",
        settings.stream_processor_relay_host,
        settings.stream_processor_relay_port,
    )
    await relay.run(on_raw_frame=lambda raw: _handle_raw_frame(relay, raw))


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
