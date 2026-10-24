"""Load test for the stream split (worker.py -> StreamProcessorRelay ->
stream_processor_worker.py -> WhaleAlertsRelay -> whale_alerts_worker.py).

Runs the REAL code of each stage -- ThetaStreamHub, StreamProcessorRelayServer/
Client, _handle_raw_frame, WhaleAlertsRelayServer, RelayDataProvider -- each in
its own OS process over real localhost sockets, fed by a fake Theta Terminal
that streams QUOTE/TRADE frames over a real WebSocket at a target rate. Nothing
touches Postgres or the real Terminal, so it is safe to run on any machine.

It measures what the 2026-10-02 incident was about: whether every stage keeps
up (no loss between stages), how late a marker frame arrives end to end, and
how long each process's event loop is blocked (the thing that makes the
`websockets` ping time out and Theta Terminal log SLOW CONSUMER).

    python -m backend.scripts.loadtest_stream_split --rates 10000,20000,40000 --seconds 30

--repeat-share 0.85 makes that share of the option QUOTEs repeats (same bid/ask as the contract's previous quote, as
at a real open, where ~85 % only change sizes); the processor drops them before the whale relay, so the expected whale
quotes exclude them. --upstream-dedupe says something in front of the hub already drops them (the native stream proxy,
via LOADTEST_HUB_URL), so the processor should handle only the non-repeats. Valid from ~10,000 frames/s up: below that
a contract's run of repeats can outlast the 1.0 s refresh and one gets forwarded again.

Not covered: the real worker's other work (REST reconcile, price persistence,
15 symbols' subscribers) and real Terminal behaviour. A pass here means the
split has headroom at that rate, not that production is verified.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import multiprocessing as mp
import os
import statistics
import time
from datetime import date
from decimal import Decimal

HOST = "127.0.0.1"
TERMINAL_PORT = 26520
STREAM_RELAY_PORT = 26601
WHALE_RELAY_PORT = 26599
CONTRACTS = 764  # what the production worker subscribes today
QUOTE_SHARE = 0.86  # option QUOTEs vs option TRADEs, roughly the real mix
# OPRA trade-condition code on every fake option TRADE, in the share measured on the 2026-10-06 09:30-09:32 raw capture
# (18: 55%, 125: 26%, 130: 9%, 95: 4%, 131: 3%, 134: 2%, 126: 1%) -- the stream processor and the relay now carry it.
_TRADE_CONDITIONS = [18] * 55 + [125] * 26 + [130] * 9 + [95] * 4 + [131] * 3 + [134] * 2 + [126]


# ----------------------------------------------------------------- helpers
class _CountingHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.counts: dict[str, int] = {}
        self.samples: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        msg = record.getMessage()
        key = "queue_full" if "queue full" in msg else record.levelname
        self.counts[key] = self.counts.get(key, 0) + 1
        if key != "queue_full" and len(self.samples) < 3:
            self.samples.append(msg[:140])


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * pct / 100))]


async def _loop_lag_monitor(samples: list[float], stop: asyncio.Event) -> None:
    """How late a 10ms timer fires = how long the loop was blocked."""
    while not stop.is_set():
        started = time.perf_counter()
        await asyncio.sleep(0.01)
        samples.append((time.perf_counter() - started - 0.01) * 1000)


def _lag_summary(samples: list[float]) -> dict[str, float]:
    return {
        "p50_ms": round(_percentile(samples, 50), 1),
        "p99_ms": round(_percentile(samples, 99), 1),
        "max_ms": round(max(samples, default=0.0), 1),
    }


def _quote_steps(count: int, repeat_share: float) -> dict[int, int]:
    """Ask step (0-6) of each quote position in a pool of `count` frames, cycled by the feeder.

    Without repeats the step follows the position, so a contract never sees the same bid/ask twice in a row. With
    repeats, a contract's k-th quote in the pool takes step floor(k * (1 - repeat_share)) % 7, so it changes on
    ~(1 - repeat_share) of its quotes; the last quote of the pool is forced off the first one's step, so the cycle
    seam is never a repeat and the very first quote sent (no previous one) is counted right too."""
    if repeat_share <= 0:
        return {i: i % 7 for i in range(count) if (i % 100) < QUOTE_SHARE * 100}
    per_contract: dict[int, list[int]] = {}
    for i in range(count):
        if (i % 100) < QUOTE_SHARE * 100:
            per_contract.setdefault(i % CONTRACTS, []).append(i)
    steps: dict[int, int] = {}
    for positions in per_contract.values():
        for k, i in enumerate(positions):
            steps[i] = int(k * (1 - repeat_share)) % 7
        if len(positions) > 1 and steps[positions[-1]] == steps[positions[0]]:
            steps[positions[-1]] = (steps[positions[0]] + 1) % 7
            if steps[positions[-1]] == steps[positions[-2]] and len(positions) > 2:
                steps[positions[-1]] = (steps[positions[0]] + 2) % 7
    return steps


def _option_frames(count: int, repeat_share: float = 0.0) -> list[tuple[str, str]]:
    """Kinds: "quote" (a new bid/ask for its contract), "repeat" (same bid/ask as its previous quote), "trade"."""
    frames: list[tuple[str, str]] = []
    steps = _quote_steps(count, repeat_share)
    last_step: dict[int, int] = {}
    if repeat_share > 0:   # the pool is cycled: a contract's first quote follows its last one of the previous cycle
        for i in sorted(steps):
            last_step[i % CONTRACTS] = steps[i]
    for i in range(count):
        contract = {
            "security_type": "OPTION",
            "root": "SPY",
            "expiration": 20261016,
            "strike": 770000 + (i % CONTRACTS) * 500,
            "right": "C" if i % 2 else "P",
        }
        if i in steps:
            step = steps[i]
            kind = "repeat" if last_step.get(i % CONTRACTS) == step else "quote"
            last_step[i % CONTRACTS] = step
            frames.append(
                (
                    kind,
                    json.dumps(
                        {
                            "header": {"type": "QUOTE", "status": "CONNECTED"},
                            "contract": contract,
                            "quote": {"bid": 1.08, "ask": round(1.09 + step * 0.01, 2)},
                        }
                    ),
                )
            )
        else:
            frames.append(
                (
                    "trade",
                    json.dumps(
                        {
                            "header": {"type": "TRADE", "status": "CONNECTED"},
                            "contract": contract,
                            "trade": {"size": 1 + i % 20, "price": 1.09, "condition": _TRADE_CONDITIONS[(i // 100) % 100]},
                        }
                    ),
                )
            )
    return frames


def _marker_frame() -> str:
    # An underlying TRADE whose price encodes the send time; the processor
    # decodes it to measure end-to-end latency through every stage.
    return json.dumps(
        {
            "header": {"type": "TRADE", "status": "CONNECTED"},
            "contract": {"security_type": "STOCK", "root": "SPY"},
            "trade": {"size": 1, "price": round(time.time() % 100000, 3)},
        }
    )


# ------------------------------------------------------------------- roles
def terminal_role(
    rate: int, seconds: int, results: mp.Queue, stop: mp.Event, go: mp.Event, repeat_share: float = 0.0
) -> None:
    from websockets.asyncio.server import serve

    expected_subscriptions = CONTRACTS * 2 + 1

    async def main() -> None:
        # with repeats, each contract needs ~100 quotes per pool for the share to come out right
        pool = _option_frames(CONTRACTS * 100 if repeat_share > 0 else 5000, repeat_share)
        sent = {"quote": 0, "repeat": 0, "trade": 0, "marker": 0}
        report: dict[str, object] = {}
        feed_done = asyncio.Event()

        async def feeder(ws) -> None:
            started = time.perf_counter()
            last_marker = 0.0
            index = 0
            while time.perf_counter() - started < seconds:
                due = int((time.perf_counter() - started) * rate) - sum(
                    sent[k] for k in ("quote", "repeat", "trade")
                )
                for _ in range(min(max(due, 0), 1500)):
                    kind, frame = pool[index % len(pool)]
                    index += 1
                    await ws.send(frame)
                    sent[kind] += 1
                now = time.perf_counter()
                if now - last_marker >= 0.05:
                    last_marker = now
                    await ws.send(_marker_frame())
                    sent["marker"] += 1
                await asyncio.sleep(0)
            report["elapsed"] = time.perf_counter() - started
            feed_done.set()

        async def handler(ws) -> None:
            subscriptions = 0
            feeding = None

            async def status() -> None:
                while True:
                    await ws.send(
                        json.dumps({"header": {"type": "STATUS", "status": "CONNECTED"}})
                    )
                    await asyncio.sleep(0.2)

            status_task = asyncio.create_task(status())
            try:
                async for raw in ws:
                    message = json.loads(raw)
                    if message.get("msg_type") != "STREAM":
                        continue
                    subscriptions += 1
                    await ws.send(
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
                    if subscriptions == expected_subscriptions and feeding is None:
                        # wait until the stream processor is connected to the
                        # worker's relay, otherwise the hub handles frames
                        # in-process (its fallback) and nothing is measured
                        while not go.is_set():
                            await asyncio.sleep(0.05)
                        await asyncio.sleep(0.5)
                        feeding = asyncio.create_task(feeder(ws))
            except Exception:  # noqa: BLE001 -- connection closed at shutdown
                pass
            finally:
                status_task.cancel()

        async with serve(handler, HOST, TERMINAL_PORT, max_size=None):
            while not feed_done.is_set() and not stop.is_set():
                await asyncio.sleep(0.1)
            await asyncio.sleep(0.5)
        elapsed = float(report.get("elapsed", seconds))
        results.put(
            {
                "role": "terminal",
                "sent_quotes": sent["quote"] + sent["repeat"],
                "sent_repeats": sent["repeat"],
                "sent_trades": sent["trade"],
                "markers": sent["marker"],
                "achieved_rate": round((sent["quote"] + sent["repeat"] + sent["trade"]) / elapsed),
                "cpu_s": round(time.process_time(), 1),
            }
        )

    asyncio.run(main())


def worker_role(seconds: int, results: mp.Queue, stop: mp.Event, go: mp.Event) -> None:
    import httpx

    from backend.adapters.providers.thetadata import provider as provider_module
    from backend.adapters.providers.thetadata.provider import ThetaStreamHub
    from backend.core.stream_processor_relay import StreamProcessorRelayServer
    from backend.domain.entities import ContractType, UnderlyingKind

    counter = _CountingHandler()
    logging.getLogger().addHandler(counter)
    logging.getLogger().setLevel(logging.WARNING)

    async def main() -> None:
        # LOADTEST_HUB_URL points the hub somewhere else, e.g. the native stream proxy (tools/stream_proxy) listening in
        # front of this fake Terminal: StreamProxy run --listen-port 26521 --upstream ws://127.0.0.1:26520/v1/events
        hub = ThetaStreamHub(
            os.environ.get("LOADTEST_HUB_URL", f"ws://{HOST}:{TERMINAL_PORT}/v1/events"),
            httpx.Client(base_url="http://127.0.0.1:1"),
        )
        for i in range(CONTRACTS):
            hub.register_contract(
                f"SPY261016{'C' if i % 2 else 'P'}{(770000 + i * 500) // 1000:05d}000",
                "SPY",
                date(2026, 10, 16),
                ContractType.CALL if i % 2 else ContractType.PUT,
                Decimal(770 + i // 2),
            )
        hub.register_symbol("SPY", UnderlyingKind.EQUITY)
        relay = StreamProcessorRelayServer(HOST, STREAM_RELAY_PORT)
        await relay.start()
        hub.set_processor_relay(relay)
        hub.start()
        lag: list[float] = []
        stop_loop = asyncio.Event()
        monitor = asyncio.create_task(_loop_lag_monitor(lag, stop_loop))
        depth_max = 0
        started_at = None
        while not stop.is_set():
            depth_max = max(depth_max, hub._message_queue.qsize())
            if relay.has_client and not go.is_set():
                go.set()
            if started_at is None and go.is_set() and hub._frames_enqueued > 3000:
                started_at = time.process_time()
                lag.clear()  # ignore connection/subscribe setup
            await asyncio.sleep(0.05)
        stop_loop.set()
        await monitor
        await hub.stop()
        await relay.stop()
        results.put(
            {
                "role": "worker",
                "frames_in": hub._frames_enqueued,
                "frames_processed": hub._frames_dequeued,
                "queue_depth_max": depth_max,
                "loop_lag": _lag_summary(lag),
                "log_counts": counter.counts,
                "log_samples": counter.samples,
                "cpu_s": round(time.process_time() - (started_at or 0), 1),
            }
        )

    # keep the provider module referenced so a future constant tweak is obvious
    _ = provider_module
    _run_maybe_profiled("worker", main, results)


def _run_maybe_profiled(role: str, main, results: mp.Queue) -> None:
    """LOADTEST_PROFILE=<role> prints that role's top functions by own time."""
    import os

    if os.environ.get("LOADTEST_PROFILE") != role:
        asyncio.run(main())
        return
    import cProfile
    import io
    import pstats

    profiler = cProfile.Profile()
    profiler.enable()
    asyncio.run(main())
    profiler.disable()
    out = io.StringIO()
    pstats.Stats(profiler, stream=out).sort_stats("tottime").print_stats(14)
    results.put({"role": f"profile_{role}", "text": out.getvalue()})


def processor_role(seconds: int, results: mp.Queue, stop: mp.Event) -> None:
    from backend.core.stream_processor_relay import StreamProcessorRelayClient
    from backend.core.whale_alerts_relay import WhaleAlertsRelayServer
    from backend.stream_processor_worker import _handle_raw_frame, _ProcessorState

    counter = _CountingHandler()
    logging.getLogger().addHandler(counter)
    logging.getLogger().setLevel(logging.WARNING)
    latencies: list[float] = []

    class _Price:
        async def persist_if_due(self, event) -> None:
            latencies.append((time.time() % 100000 - float(event.price)) * 1000)

    async def main() -> None:
        whale = WhaleAlertsRelayServer(HOST, WHALE_RELAY_PORT)
        await whale.start()
        state = _ProcessorState(whale, _Price())  # type: ignore[arg-type]
        handled = 0

        def on_frame(raw: str) -> None:
            nonlocal handled
            handled += 1
            _handle_raw_frame(state, raw)

        client = StreamProcessorRelayClient(HOST, STREAM_RELAY_PORT)
        task = asyncio.create_task(client.run(on_raw_frame=on_frame))
        lag: list[float] = []
        stop_loop = asyncio.Event()
        monitor = asyncio.create_task(_loop_lag_monitor(lag, stop_loop))
        started_at = None
        while not stop.is_set():
            if started_at is None and handled > 1000:
                started_at = time.process_time()
                lag.clear()
            await asyncio.sleep(0.05)
        stop_loop.set()
        await monitor
        task.cancel()
        await whale.stop()
        results.put(
            {
                "role": "processor",
                "frames_handled": handled,
                "marker_latency_ms": {
                    "n": len(latencies),
                    "p50": round(_percentile(latencies, 50), 1),
                    "p99": round(_percentile(latencies, 99), 1),
                    "max": round(max(latencies, default=0.0), 1),
                    "avg": round(statistics.fmean(latencies), 1) if latencies else 0.0,
                },
                "loop_lag": _lag_summary(lag),
                "log_counts": counter.counts,
                "log_samples": counter.samples,
                "cpu_s": round(time.process_time() - (started_at or 0), 1),
            }
        )

    asyncio.run(main())


def consumer_role(seconds: int, results: mp.Queue, stop: mp.Event) -> None:
    from backend.core.whale_alerts_relay import RelayDataProvider

    counter = _CountingHandler()
    logging.getLogger().addHandler(counter)
    logging.getLogger().setLevel(logging.WARNING)

    async def main() -> None:
        provider = RelayDataProvider(HOST, WHALE_RELAY_PORT)
        await provider.start()
        counts = {"quotes": 0, "trades": 0}

        async def drain_quotes() -> None:
            async for _ in provider.stream_quotes("SPY"):
                counts["quotes"] += 1

        async def drain_trades() -> None:
            async for _ in provider.stream_trades("SPY"):
                counts["trades"] += 1

        tasks = [asyncio.create_task(drain_quotes()), asyncio.create_task(drain_trades())]
        lag: list[float] = []
        stop_loop = asyncio.Event()
        monitor = asyncio.create_task(_loop_lag_monitor(lag, stop_loop))
        started_at = None
        while not stop.is_set():
            if started_at is None and counts["quotes"] > 1000:
                started_at = time.process_time()
                lag.clear()
            await asyncio.sleep(0.05)
        stop_loop.set()
        await monitor
        for task in tasks:
            task.cancel()
        await provider.stop()
        results.put(
            {
                "role": "consumer",
                "quotes": counts["quotes"],
                "trades": counts["trades"],
                "loop_lag": _lag_summary(lag),
                "log_counts": counter.counts,
                "cpu_s": round(time.process_time() - (started_at or 0), 1),
            }
        )

    asyncio.run(main())


# -------------------------------------------------------------------- main
def run_step(rate: int, seconds: int, repeat_share: float = 0.0) -> dict[str, dict]:
    ctx = mp.get_context("spawn")
    results: mp.Queue = ctx.Queue()
    stop = ctx.Event()
    go = ctx.Event()
    procs = [
        ctx.Process(target=terminal_role, args=(rate, seconds, results, stop, go, repeat_share)),
        ctx.Process(target=processor_role, args=(seconds, results, stop)),
        ctx.Process(target=consumer_role, args=(seconds, results, stop)),
    ]
    expected_results = 4 + (1 if os.environ.get("LOADTEST_PROFILE") else 0)
    for p in procs:
        p.start()
    time.sleep(2.5)
    worker = ctx.Process(target=worker_role, args=(seconds, results, stop, go))
    worker.start()
    procs.append(worker)
    terminal = procs[0]
    terminal.join(timeout=seconds + 90)
    time.sleep(4)  # let in-flight frames drain through the stages
    stop.set()
    out: dict[str, dict] = {}
    deadline = time.time() + 30
    while len(out) < expected_results and time.time() < deadline:
        try:
            item = results.get(timeout=1)
            out[item["role"]] = item
        except Exception:  # noqa: BLE001 -- queue empty
            pass
    for p in procs:
        p.join(timeout=10)
        if p.is_alive():
            p.terminate()
    return out


def report(rate: int, seconds: int, r: dict[str, dict], upstream_dedupe: bool = False) -> bool:
    t, w, p, c = (r.get(k, {}) for k in ("terminal", "worker", "processor", "consumer"))
    for key, item in r.items():
        if key.startswith("profile_"):
            print(f"\n--- {key} ---\n{item['text']}")
    if not all(r.get(k) for k in ("terminal", "worker", "processor", "consumer")):
        print(f"\n== target {rate:,}/s: missing results from {sorted({'terminal','worker','processor','consumer'} - set(r))}")
        return False
    sent = t["sent_quotes"] + t["sent_trades"] + t["markers"]
    repeats = t.get("sent_repeats", 0)
    # repeats never reach the whale relay (the processor drops them), nor the processor when a proxy drops them first
    expected_proc = sent - (repeats if upstream_dedupe else 0)
    expected_quotes = t["sent_quotes"] - repeats
    lost_proc = expected_proc - p["frames_handled"]
    lost_quotes = expected_quotes - c["quotes"]
    lost_trades = t["sent_trades"] - c["trades"]
    print(f"\n== target {rate:,}/s for {seconds}s  (terminal achieved {t['achieved_rate']:,}/s)")
    if repeats:
        print(f"   repeated quotes sent {repeats:,} ({100 * repeats / max(1, t['sent_quotes']):.1f} % of quotes), "
              f"dropped {'before the hub (upstream dedupe)' if upstream_dedupe else 'by the processor'}")
    print(f"   data frames: sent {sent:,} | processor handled {p['frames_handled']:,} of {expected_proc:,} expected (lost {lost_proc:,})")
    print(f"   whale events: quotes {c['quotes']:,}/{expected_quotes:,} (lost {lost_quotes:,}) | "
          f"trades {c['trades']:,}/{t['sent_trades']:,} (lost {lost_trades:,})")
    print(f"   marker end-to-end latency ms: {p['marker_latency_ms']}")
    print(f"   event-loop lag ms  worker {w['loop_lag']} | processor {p['loop_lag']} | consumer {c['loop_lag']}")
    print(f"   hub queue depth max: {w['queue_depth_max']:,} | CPU s (of {seconds}): terminal {t['cpu_s']} "
          f"worker {w['cpu_s']} processor {p['cpu_s']} consumer {c['cpu_s']}")
    print(f"   log counts: worker {w['log_counts']} processor {p['log_counts']} consumer {c['log_counts']}")
    for name, item in (("worker", w), ("processor", p)):
        if item.get("log_samples"):
            print(f"   {name} sample warnings: {item['log_samples']}")
    ok = (
        lost_proc <= 0 and lost_quotes <= 0 and lost_trades <= 0
        and not w["log_counts"].get("queue_full") and not p["log_counts"].get("queue_full")
        and not c["log_counts"].get("queue_full")
    )
    print("   RESULT:", "no loss, no queue-full" if ok else "LOSS or QUEUE-FULL -- see above")
    return ok


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rates", default="10000,20000,40000")
    parser.add_argument("--seconds", type=int, default=30)
    parser.add_argument("--repeat-share", type=float, default=0.0,
                        help="share of option QUOTEs that repeat the contract's previous bid/ask (real open ~0.85)")
    parser.add_argument("--upstream-dedupe", action="store_true",
                        help="something in front of the hub (the native stream proxy) drops the repeated quotes")
    args = parser.parse_args()
    all_ok = True
    for rate in (int(x) for x in args.rates.split(",")):
        all_ok &= report(rate, args.seconds, run_step(rate, args.seconds, args.repeat_share), args.upstream_dedupe)
    print("\nOVERALL:", "PASS" if all_ok else "FAIL")


if __name__ == "__main__":
    main()
