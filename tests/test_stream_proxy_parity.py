"""The native stream proxy (tools/stream_proxy) drops repeated quotes BEFORE Python sees them, so its rule must be the
same as the one the stream processor applies today (_ProcessorState.is_repeated_quote). This feeds one generated
sequence of frames, with timestamps, to both and compares every decision. Skipped when the proxy is not built
(`dotnet build -c Release` in tools/stream_proxy)."""

from __future__ import annotations

import json
import random
import shutil
import subprocess
from pathlib import Path

import pytest

import backend.stream_processor_worker as worker_module
from backend.stream_processor_worker import _ProcessorState

PROXY_DLL = Path(__file__).resolve().parents[1] / "tools" / "stream_proxy" / "bin" / "Release" / "net10.0" / "StreamProxy.dll"


def _frames(count: int) -> list[tuple[float, str]]:
    rng = random.Random(23)
    contracts = [
        {"security_type": "OPTION", "root": root, "expiration": 20261023, "strike": strike, "right": right}
        for root in ("SPXW", "SPY")
        for strike in (7600000, 7605000, 7610000, 760000)
        for right in ("C", "P")
    ]
    last = {i: (1.0, 1.1) for i in range(len(contracts))}
    now = 0.0
    out: list[tuple[float, str]] = []
    for _ in range(count):
        # continuous steps: with round steps (0.2 + 0.6 + 0.2) two clocks can land exactly on the 1.0 s boundary and round it
        # differently (float seconds vs integer ticks), which never happens with real timestamps
        now += rng.uniform(0.0005, 0.7) if rng.random() < 0.5 else rng.uniform(0.0001, 0.02)
        i = rng.randrange(len(contracts))
        kind = rng.random()
        if kind < 0.05:
            out.append((now, json.dumps({"header": {"type": "TRADE", "status": "CONNECTED"}, "contract": contracts[i], "trade": {"price": 1.05, "size": 3}})))
            continue
        if kind < 0.07:
            out.append((now, json.dumps({"header": {"type": "STATUS", "status": "CONNECTED"}})))
            continue
        bid, ask = last[i]
        if rng.random() > 0.8:
            bid, ask = round(bid + rng.choice((-0.05, 0.05)), 2), round(ask + rng.choice((-0.05, 0.05)), 2)
            last[i] = (bid, ask)
        quote: dict[str, object] = {"bid_size": rng.randint(1, 100), "bid": bid, "ask_size": rng.randint(1, 100), "ask": ask}
        if rng.random() < 0.02:
            del quote["bid"]
        out.append((now, json.dumps({"header": {"type": "QUOTE", "status": "CONNECTED"}, "contract": contracts[i], "quote": quote})))
    return out


@pytest.mark.skipif(shutil.which("dotnet") is None or not PROXY_DLL.exists(), reason="native stream proxy not built")
def test_proxy_drops_exactly_the_quotes_the_processor_drops(tmp_path, monkeypatch):
    frames = _frames(20000)
    path = tmp_path / "frames.tsv"
    path.write_text("".join(f"{t:.6f}\t{frame}\n" for t, frame in frames), encoding="utf-8")
    result = subprocess.run(
        ["dotnet", str(PROXY_DLL), "dedupe-check", "--file", str(path), "--refresh", str(worker_module.QUOTE_REFRESH_SECONDS)],
        capture_output=True, text=True, check=True, timeout=120,
    )
    proxy = [line == "1" for line in result.stdout.split()]

    state = _ProcessorState(whale_alerts_relay=None, price_use_case=None)  # type: ignore[arg-type]
    clock = {"now": 0.0}
    monkeypatch.setattr(worker_module.time, "monotonic", lambda: clock["now"])
    python = []
    for t, frame in frames:
        clock["now"] = t
        message = json.loads(frame)
        is_quote = message.get("header", {}).get("type") == "QUOTE"
        python.append(is_quote and state.is_repeated_quote(message))

    assert len(proxy) == len(python)
    mismatches = [i for i, (a, b) in enumerate(zip(proxy, python)) if a != b]
    assert not mismatches, f"{len(mismatches)} mismatches, first at frame {mismatches[0]}: {frames[mismatches[0]][1]}"
    assert 0.1 < sum(python) / len(python) < 0.95  # the sequence really exercises both outcomes
