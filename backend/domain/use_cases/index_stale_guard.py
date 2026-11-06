"""Stale index guard: SPX / NDX derived from SPY / QQQ while ThetaData's index feed lags (docs/diseno-es-nq-en-vivo-ninjatrader-2026-11-06.txt, phase 1).

Why (2026-11-06): from ~09:37 ET ThetaData delivered EVERY index (SPX, NDX, VIX, RUT) 8-10 minutes late, on the stream and on REST, while
stocks and options were on time (confirmed by ThetaData on Discord, 09:46). Convexa's stored SPX price is what the chart, ES / NQ (index +
anchor), the fast gamma layer and the 0DTE heatmap read, so all of them ran 15-34 points behind; SPX index ticks also dropped from ~60 to
16-39 a minute. SPX / SPY moved +-0.002 around 10.029 all of 2026-11-05; on 11-06 it swung 9.986-10.039 while the index lagged.

The rule, per index (SPX -> SPY, NDX -> QQQ):
- healthy samples of index / proxy (both fresh) every SAMPLE_EVERY, kept for RATIO_WINDOW; their median is the "good" ratio;
- the index is STALE when its tick's exchange time is more than LAG_LIMIT behind now, when no index tick arrived for SILENCE_LIMIT while
  the proxy trades, or when the median of index / proxy over the last RECENT_WINDOW stays more than DRIFT_LIMIT away from the good ratio
  for DRIFT_HOLD (a median, not tick by tick: one tick against a proxy price a second older can be 0.05 % off on a fast move);
- while stale the index ticks are not written; every proxy tick gives index = proxy x the good ratio, frozen when the episode started;
- back to normal after RECOVER_HOLD of fresh index ticks whose recent median is aligned again.
Replayed on the stored prices of 2026-10-29..11-06 (docs/hm_index_stale_guard_replay.py): no episode on the normal days; 11-05 11:32-11:36
had 36-43 s without ANY stored row (SPY too: our own pause, not the index feed), hence SILENCE_LIMIT 60 s.
Nothing is ever invented without a good ratio (MIN_SAMPLES healthy samples): before that the guard stays out of the way.
Pure logic, no I/O: backend/stream_processor_worker.py feeds it and writes what it returns."""

from __future__ import annotations

import statistics
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

INDEX_PROXIES: dict[str, str] = {"SPX": "SPY", "NDX": "QQQ"}
LAG_LIMIT = timedelta(seconds=20)
SILENCE_LIMIT = timedelta(seconds=60)
RECENT_WINDOW = timedelta(seconds=60)
DRIFT_LIMIT = 0.0005
DRIFT_HOLD = timedelta(seconds=60)
RECOVER_HOLD = timedelta(seconds=180)
SAMPLE_EVERY = timedelta(seconds=5)
RATIO_WINDOW = timedelta(minutes=30)
MIN_SAMPLES = 12
PROXY_FRESH = timedelta(seconds=3)


@dataclass(frozen=True, slots=True)
class IndexPriceFallback:
    """The stored state of one index (table index_price_fallback, migration 0054): written by the stream processor at every change and every
    STATUS_HEARTBEAT while active, read by GET /api/v1/market/price-sources for the banner. A row whose updated_at is older than STATUS_STALE
    is not trusted as active (the processor stopped while an episode was open)."""

    symbol: str
    proxy: str
    active: bool
    ratio: float
    reason: str
    since: datetime
    updated_at: datetime


STATUS_HEARTBEAT = timedelta(seconds=15)
STATUS_STALE = timedelta(seconds=90)


def fallback_params(status: IndexPriceFallback) -> dict[str, object]:
    """Bind parameters of the index_price_fallback upsert (both Postgres adapters)."""
    return {
        "symbol": status.symbol.upper(),
        "proxy": status.proxy,
        "active": status.active,
        "ratio": status.ratio,
        "reason": status.reason,
        "since": status.since,
        "updated_at": status.updated_at,
    }


def fallback_from_row(row) -> IndexPriceFallback:
    return IndexPriceFallback(
        symbol=str(row["symbol"]),
        proxy=str(row["proxy"]),
        active=bool(row["active"]),
        ratio=float(row["ratio"]),
        reason=str(row["reason"]),
        since=row["since"],
        updated_at=row["updated_at"],
    )


@dataclass(frozen=True, slots=True)
class FallbackChange:
    """An episode starting (active) or ending, for the log, the status row and the alert."""

    symbol: str
    proxy: str
    active: bool
    ratio: float
    at: datetime
    reason: str


@dataclass(slots=True)
class _IndexState:
    proxy: str
    samples: deque[tuple[datetime, float]] = field(default_factory=deque)
    recent: deque[tuple[datetime, float]] = field(default_factory=deque)
    last_sample_at: datetime | None = None
    active_ratio: float | None = None
    reason: str = ""
    drift_since: datetime | None = None
    healthy_since: datetime | None = None
    last_tick_at: datetime | None = None


class IndexStaleGuard:
    def __init__(self, proxies: dict[str, str] | None = None) -> None:
        self._states = {index: _IndexState(proxy=proxy) for index, proxy in (proxies or INDEX_PROXIES).items()}
        self._index_by_proxy = {state.proxy: index for index, state in self._states.items()}
        self._proxy_prices: dict[str, tuple[datetime, float]] = {}
        self._changes: list[FallbackChange] = []

    def watches(self, symbol: str) -> bool:
        return symbol in self._states or symbol in self._index_by_proxy

    def active(self, index: str) -> bool:
        state = self._states.get(index)
        return state is not None and state.active_ratio is not None

    def status(self, index: str, since: datetime, now: datetime) -> IndexPriceFallback | None:
        """The row to keep for an index whose episode is open (the heartbeat), None once it closed."""
        state = self._states.get(index)
        if state is None or state.active_ratio is None:
            return None
        return IndexPriceFallback(index, state.proxy, True, state.active_ratio, state.reason, since, now)

    def drain_changes(self) -> list[FallbackChange]:
        changes, self._changes = self._changes, []
        return changes

    def observe_index(self, index: str, price: Decimal, exchange_ts: datetime | None, now: datetime) -> bool:
        """One index tick. True when it may be written as the index's price; False while the index is replaced by its proxy."""
        state = self._states.get(index)
        if state is None:
            return True
        state.last_tick_at = now
        lagging = exchange_ts is not None and now - exchange_ts > LAG_LIMIT
        proxy = self._fresh_proxy(state.proxy, now)
        ratio = float(price) / proxy if proxy else None
        if ratio is not None:
            state.recent.append((now, ratio))
        while state.recent and now - state.recent[0][0] > RECENT_WINDOW:
            state.recent.popleft()
        recent = statistics.median(value for _, value in state.recent) if state.recent else None
        good = state.active_ratio if state.active_ratio is not None else self._good_ratio(state)
        drifting = recent is not None and good is not None and abs(recent / good - 1) > DRIFT_LIMIT
        if drifting:
            state.drift_since = state.drift_since or now
        else:
            state.drift_since = None
        drift_held = state.drift_since is not None and now - state.drift_since >= DRIFT_HOLD

        if state.active_ratio is None:
            if (lagging or drift_held) and good is not None:
                self._start(index, state, good, now, "lag" if lagging else "drift")
                return False
            if not lagging and not drifting and ratio is not None:
                self._sample(state, ratio, now)
            return True

        if lagging or drifting:
            state.healthy_since = None
            return False
        state.healthy_since = state.healthy_since or now
        if now - state.healthy_since >= RECOVER_HOLD:
            self._changes.append(FallbackChange(index, state.proxy, False, state.active_ratio, now, "recovered"))
            state.active_ratio = None
            state.healthy_since = None
            state.samples.clear()
            state.last_sample_at = None
            return True
        return False

    def observe_proxy(self, proxy: str, price: Decimal, now: datetime) -> list[tuple[str, Decimal]]:
        """One proxy trade. Returns the index prices to write in its place (index, price), empty while the index is healthy."""
        self._proxy_prices[proxy] = (now, float(price))
        index = self._index_by_proxy.get(proxy)
        if index is None:
            return []
        state = self._states[index]
        if state.active_ratio is None:
            silent = state.last_tick_at is not None and now - state.last_tick_at > SILENCE_LIMIT
            good = self._good_ratio(state)
            if not silent or good is None:
                return []
            self._start(index, state, good, now, "silent")
        return [(index, Decimal(str(round(float(price) * state.active_ratio, 2))))]

    def _fresh_proxy(self, proxy: str, now: datetime) -> float | None:
        seen = self._proxy_prices.get(proxy)
        if seen is None or now - seen[0] > PROXY_FRESH or seen[1] <= 0:
            return None
        return seen[1]

    def _good_ratio(self, state: _IndexState) -> float | None:
        if len(state.samples) < MIN_SAMPLES:
            return None
        return statistics.median(ratio for _, ratio in state.samples)

    def _sample(self, state: _IndexState, ratio: float, now: datetime) -> None:
        if state.last_sample_at is not None and now - state.last_sample_at < SAMPLE_EVERY:
            return
        state.last_sample_at = now
        state.samples.append((now, ratio))
        while state.samples and now - state.samples[0][0] > RATIO_WINDOW:
            state.samples.popleft()

    def _start(self, index: str, state: _IndexState, ratio: float, now: datetime, reason: str) -> None:
        state.active_ratio = ratio
        state.reason = reason
        state.drift_since = None
        state.healthy_since = None
        self._changes.append(FallbackChange(index, state.proxy, True, ratio, now, reason))
