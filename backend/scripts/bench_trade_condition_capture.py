"""Micro-benchmark for the trade-condition capture (WhaleAlertsEngine.process_trade with store_conditions on vs off).

process_trade() is the per-trade hot path of the whale-alerts process, so this measures what the capture adds to it: CPU per
trade and the memory the per-contract state holds afterwards. Same trades, same engine class, only the flag differs; the two
variants are interleaved over several rounds so a noisy machine hits both alike.

    python -m backend.scripts.bench_trade_condition_capture --contracts 2000 --trades 400000 --rounds 5

It uses InMemoryStorage (no Postgres) and a fixed quote, so it isolates the engine's own work. It does NOT cover the websocket
stack or the stream processor -- for those use backend/scripts/loadtest_stream_split.py (its fake trades now carry a condition).
"""

from __future__ import annotations

import argparse
import gc
import random
import statistics
import time
import tracemalloc
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from backend.adapters.storage.memory import InMemoryStorage
from backend.domain.entities import FlowEvent, FlowEventType, LatestQuote, Side
from backend.domain.use_cases import WhaleAlertsEngine

# Condition-code mix measured on the 2026-10-06 09:30-09:32 raw capture (share of option TRADE frames, all roots).
CONDITION_MIX = [(18, 55.3), (125, 26.0), (130, 9.2), (95, 4.0), (131, 3.4), (134, 2.0), (126, 0.05), (137, 0.02)]
BASE_TIME = datetime(2026, 10, 7, 14, 0, tzinfo=UTC)  # 10:00 ET, regular session
QUOTE = LatestQuote(bid=Decimal("0.01"), ask=Decimal("0.02"), as_of=BASE_TIME)


def _events(contracts: int, trades: int, minutes: int, seed: int) -> list[FlowEvent]:
    rng = random.Random(seed)
    codes = [c for c, _ in CONDITION_MIX]
    weights = [w for _, w in CONDITION_MIX]
    occ = [f"SPXW261008P{7000 + i * 5:05d}000" for i in range(contracts)]
    out = []
    for i in range(trades):
        minute = i * minutes // trades
        out.append(
            FlowEvent(
                symbol="SPX",
                occ_symbol=occ[rng.randrange(contracts)],
                as_of=BASE_TIME + timedelta(minutes=minute, seconds=rng.randrange(60)),
                event_type=FlowEventType.UNUSUAL,
                premium=Decimal(rng.randrange(100, 20000)),
                size=rng.randrange(1, 30),
                aggressor_side=Side.UNKNOWN,
                condition=rng.choices(codes, weights)[0],
            )
        )
    out.sort(key=lambda e: e.as_of)
    return out


def _run(events: list[FlowEvent], store_conditions: bool) -> tuple[float, int, int]:
    gc.collect()
    engine = WhaleAlertsEngine(InMemoryStorage(), store_conditions=store_conditions)
    tracemalloc.start()
    started = time.perf_counter()
    for event in events:
        engine.process_trade(event, QUOTE)
    elapsed = time.perf_counter() - started
    current, _peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    alerts = len(engine.recent_alerts("SPX", 100000))
    return elapsed, current, alerts


def _cpu_only(events: list[FlowEvent], store_conditions: bool) -> float:
    gc.collect()
    engine = WhaleAlertsEngine(InMemoryStorage(), store_conditions=store_conditions)
    started = time.perf_counter()
    for event in events:
        engine.process_trade(event, QUOTE)
    return time.perf_counter() - started


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contracts", type=int, default=2000)
    parser.add_argument("--trades", type=int, default=400_000)
    parser.add_argument("--minutes", type=int, default=30, help="minutes of market time the trades are spread over")
    parser.add_argument("--rounds", type=int, default=5)
    args = parser.parse_args()
    events = _events(args.contracts, args.trades, args.minutes, seed=7)
    print(f"{args.trades:,} trades over {args.contracts:,} contracts and {args.minutes} minutes; condition mix {CONDITION_MIX}")

    off_times: list[float] = []
    on_times: list[float] = []
    for round_no in range(args.rounds):
        pair = [(False, off_times), (True, on_times)]
        if round_no % 2:
            pair.reverse()
        for flag, sink in pair:
            sink.append(_cpu_only(events, flag))
    off_med, on_med = statistics.median(off_times), statistics.median(on_times)
    n = len(events)
    print(f"CPU, median of {args.rounds} rounds: capture OFF {1e6 * off_med / n:6.2f} us/trade | capture ON {1e6 * on_med / n:6.2f} us/trade | "
          f"added {1e6 * (on_med - off_med) / n:+.2f} us/trade ({100 * (on_med / off_med - 1):+.1f}%)")
    print(f"  all rounds OFF {[round(1e6 * t / n, 2) for t in off_times]} ON {[round(1e6 * t / n, 2) for t in on_times]} (us/trade)")
    _, mem_off, alerts_off = _run(events, False)
    _, mem_on, alerts_on = _run(events, True)
    print(f"Python memory held by the engine after the run (tracemalloc): OFF {mem_off / 1e6:.1f} MB | ON {mem_on / 1e6:.1f} MB | added {(mem_on - mem_off) / 1e6:+.1f} MB "
          f"for {args.contracts:,} contracts")
    print(f"alerts produced: OFF {alerts_off} | ON {alerts_on} (must be equal: the capture changes no alert)")
    print("Reference rates: production sees ~200-1,000 option TRADE frames/s on a normal minute and ~2,400/s in the first minute of the open.")


if __name__ == "__main__":
    main()
