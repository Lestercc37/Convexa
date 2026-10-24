# Convexa stream proxy (native reader)

A small C# (.NET) process that sits between the Theta Terminal's local stream WebSocket and the Python worker:

```
Theta Terminal  ws://127.0.0.1:25520/v1/events
      |  (one client, as ThetaData requires: the proxy)
StreamProxy     ws://127.0.0.1:25521/v1/events
      |
backend.worker (ThetaStreamHub, picows reader) -> stream processor -> whale alerts
```

## Why

At the 2026-10-23 open the Terminal sent 59,000-69,000 frames/s, about 85 % of them quotes that only changed sizes
(the stream processor already drops them, but only after the worker has read and relayed them and the processor has
parsed them). The two Python stream processes ran at 75-87 % of ONE core each, and the Terminal logged 2.85 M
dropped packets (SLOW CONSUMER) between 09:30 and 10:40, in bursts that follow ~1 s silences of the Terminal: after
each silence it must flush its backlog on top of the live rate, faster than a Python reader drains it.

The proxy:

- drains the Terminal as fast as the socket allows (16 MB receive buffer, no per-frame await on the consumer side):
  the offline bench measured ~0.9-1.2 M frames/s taken from a fake Terminal sending bursts as fast as it could,
  versus ~0.1 M frames/s per core for the picows reader;
- drops repeated quotes with exactly the processor's rule (`_ProcessorState.is_repeated_quote`: same bid AND ask
  for the same contract, less than 1.0 s since the last one forwarded), so Python sees ~15 % of the frames
  (`tests/test_stream_proxy_parity.py`: 20,000 frames, 0 mismatches);
- forwards everything else untouched (TRADE, STATUS, REQ_RESPONSE, subscriptions in the other direction);
- serves ONE client at a time and closes both sides when either closes, so the worker reconnects and
  re-subscribes exactly as it does today; a client that stops reading is cut after `--max-queue` frames
  (default 2,000,000) instead of growing memory without limit.

## Build and test (no production impact)

```
cd tools/stream_proxy
dotnet build -c Release
dotnet bin/Release/net10.0/StreamProxy.dll bench --rate 70000 --seconds 20 --burst-every 5 --burst-frames 70000
python -m pytest tests/test_stream_proxy_parity.py
```

End to end with the real hub + processor + whale consumer (fake Terminal on 26520):

```
dotnet bin/Release/net10.0/StreamProxy.dll run --listen-port 26521 --upstream ws://127.0.0.1:26520/v1/events --log-every 10
LOADTEST_HUB_URL=ws://127.0.0.1:26521/v1/events python -m backend.scripts.loadtest_stream_split --rates 40000 --seconds 20
```

Result on the laptop (2026-10-23): 800,399 frames sent, 800,399 handled by the processor, 0 lost, 1,529
subscriptions passed through, marker p99 51 ms.

## Deploy (NOT done; needs Lester's yes, after the close)

1. `dotnet publish -c Release -r win-x64 --self-contained -p:PublishSingleFile=true -o publish` (the server has
   no .NET runtime; a self-contained single file needs nothing installed).
2. Copy `publish\StreamProxy.exe` to `V:\Convexa\stream_proxy\` and create the NSSM service `ConvexaStreamProxy`
   (`StreamProxy.exe run`, stdout/stderr to `V:\Convexa\repo\logs\stream_proxy.log`, auto start, restart on exit).
3. Point the worker at it: environment `QLL_THETADATA_WS_URL=ws://127.0.0.1:25521/v1/events` on `ConvexaWorker`,
   then restart `ConvexaWorker` (warn first: the stream alarm toast can fire).
4. Check: `stream_proxy.log` prints one line per minute (frames/s in, forwarded, repeats dropped, max queue,
   max upstream gap); `worker.log` connects and subscribes; the processor's frame-mix line drops to ~15 % of the
   previous rate (the repeats no longer reach it).

Rollback: remove `QLL_THETADATA_WS_URL` from `ConvexaWorker` and restart it (the worker talks to the Terminal
directly again); stop `ConvexaStreamProxy`.

## What it does not change

- ThetaData still sees one stream client per account (the proxy).
- The processor's own repeat filter stays: it becomes a no-op for most frames.
- If the ~1 s silences are inside the Terminal (JVM pauses, see the GC log enabled 2026-10-23), the proxy cannot
  remove them; it only makes the catch-up after them fast enough not to overflow the Terminal's queue.
