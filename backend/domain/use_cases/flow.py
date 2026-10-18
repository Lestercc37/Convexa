from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from backend.domain.entities import (
    ContractType,
    FlowEvent,
    GammaAggregate,
    LatestQuote,
    OptionChain,
    Side,
    WhaleThreshold,
)
from backend.domain.ports import IStorage
from backend.domain.use_cases.calculate_bvc import (
    calculate_bvc_split,
    calculate_price_volatility,
)
from backend.domain.use_cases.calculate_lee_ready import classify_trade_side
from backend.domain.use_cases.market_hours import EASTERN_TIME, is_market_open

logger = logging.getLogger(__name__)


class WhaleAlertType(StrEnum):
    UNUSUAL = "UNUSUAL"
    WHALE = "WHALE"
    SUSTAINED_FLOW = "SUSTAINED_FLOW"


class Moneyness(StrEnum):
    ITM = "ITM"
    ATM = "ATM"
    OTM = "OTM"
    # |delta| outside 0.25 to 0.85: too far from the money to be a directional bet (far OTM) or so deep in it that it trades like the
    # index itself. The heatmap design leaves these out by default ("Lejos"); only produced from a stored delta, never by the fallback.
    FAR = "FAR"


# Fallback only, used when no GammaAggregate (and therefore no ATR-based
# GammaAggregate.near_the_money_width) is cached for this symbol yet -- see
# _classify_moneyness's own docstring for the real
# band. A fixed dollar/point band would be wrong across symbols spanning
# $30 stocks to 7,000+-point indices, so this is relative -- but a FLAT
# percentage has exactly that same problem one level up: 1% of a $230
# stock is ~$2.30 (about one strike), while 1% of SPX at ~$7,682 is ~$77
# (15+ of SPX's own $5-wide weekly strikes) -- confirmed live, 2026-09-29,
# from a real SPXW put alert (strike 7655, spot ~7682, 0.35% away) the
# user flagged as nonsensically tagged ATM. Kept only as a same-day,
# gamma-not-cached-yet fallback, not the primary definition anymore.
ATM_BAND_PCT = Decimal("0.01")


def _atm_band(spot: Decimal, near_the_money_width: Decimal | None) -> Decimal:
    """The real definition now lives on GammaAggregate.near_the_money_width
    -- the same ATR-based, per-symbol-scale-aware half-width
    CalculateGammaExposureOrchestrator already computes every cycle to
    select Call Wall/Put Wall/Net GEX's own narrow strike window (see
    calculate_near_the_money_width.py). Falls back to ATM_BAND_PCT's flat
    percentage only when no GammaAggregate is cached for this symbol yet
    (or it predates migration 0039) -- never worse than the old behavior,
    just not volatility-aware in that narrow window."""
    if near_the_money_width is not None and near_the_money_width > 0:
        return near_the_money_width
    return spot * ATM_BAND_PCT


def _classify_moneyness(
    contract_type: ContractType,
    strike: Decimal,
    spot: Decimal | None,
    near_the_money_width: Decimal | None,
) -> Moneyness:
    """Strike vs. spot at the moment this alert fires -- a deep ITM call
    (delta near 1, trades almost like the stock/index itself) carries a
    very different signal than the same dollar amount hitting an ATM/OTM
    strike (genuine leveraged directional exposure, the kind that forces
    real dealer gamma hedging) -- see this module's own new docstring
    addition on WhaleAlert.moneyness for why raw premium alone doesn't
    distinguish these. `spot=None` (no market price recorded for this
    symbol yet -- vanishingly rare in practice, since the REST scheduler
    seeds market_snapshots well before any trade stream classification
    starts) falls back to ATM rather than guessing ITM/OTM from nothing.
    See _atm_band for how close counts as ATM."""
    if spot is None or spot <= 0:
        return Moneyness.ATM
    distance = abs(strike - spot)
    if distance <= _atm_band(spot, near_the_money_width):
        return Moneyness.ATM
    in_the_money = strike < spot if contract_type == ContractType.CALL else strike > spot
    return Moneyness.ITM if in_the_money else Moneyness.OTM


# Moneyness from the contract's own |delta|, the zones Lester and I settled for the heatmap's alert cubes (2026-10-16): only deltas from
# 0.25 to 0.85 count as a directional bet, split OTM 0.25-0.40, ATM 0.40-0.60, ITM 0.60-0.85; anything outside that range is FAR ("Lejos").
# The strike-vs-ATR-width rule above stays as the fallback when no delta is stored.
FAR_DELTA_BELOW = Decimal("0.25")
OTM_DELTA_BELOW = Decimal("0.40")
ITM_DELTA_MIN = Decimal("0.60")
FAR_DELTA_ABOVE = Decimal("0.85")

# A strike counts as "at" a gamma level when it lies within this fraction of the ATR-based near_the_money_width (SPX 94.69 -> about
# 9.5 points, two strikes). The previous tag measured SPOT against half that width (about 47 points) and was on for every alert.
STRIKE_NEAR_LEVEL_WIDTH_RATIO = Decimal("0.1")
STRIKE_NEAR_LEVEL_BAND_PCT = Decimal("0.001")


def _moneyness_from_delta(delta: Decimal | None) -> Moneyness | None:
    """None when there is no usable delta (missing, or 0 as ThetaData sends for contracts without an IV solution)."""
    if delta is None:
        return None
    magnitude = abs(delta)
    if magnitude <= 0:
        return None
    if magnitude < FAR_DELTA_BELOW or magnitude > FAR_DELTA_ABOVE:
        return Moneyness.FAR
    if magnitude >= ITM_DELTA_MIN:
        return Moneyness.ITM
    if magnitude >= OTM_DELTA_BELOW:
        return Moneyness.ATM
    return Moneyness.OTM


def _nearest_level_to_strike(strike: Decimal, spot: Decimal | None, gamma: GammaAggregate | None) -> str | None:
    """Which of Call Wall / Put Wall / Gamma Flip (Tactical) the contract's STRIKE sits at, if any. Measured against the strike, not
    the spot: the spot is what the chart's current price already shows, while a card is about one contract."""
    if gamma is None:
        return None
    if gamma.near_the_money_width is not None and gamma.near_the_money_width > 0:
        band = gamma.near_the_money_width * STRIKE_NEAR_LEVEL_WIDTH_RATIO
    elif spot is not None and spot > 0:
        band = spot * STRIKE_NEAR_LEVEL_BAND_PCT
    else:
        return None
    best_label: str | None = None
    best_distance: Decimal | None = None
    for label, level in (("Call Wall", gamma.call_wall), ("Put Wall", gamma.put_wall), ("Gamma Flip", gamma.gamma_flip)):
        if level is None:
            continue
        distance = abs(level - strike)
        if distance <= band and (best_distance is None or distance < best_distance):
            best_distance = distance
            best_label = label
    return best_label


@dataclass(frozen=True, slots=True)
class WhaleAlertThresholds:
    unusual_min: Decimal = Decimal("40000.0")
    whale_min: Decimal = Decimal("150000.0")
    unusual_multiplier: Decimal = Decimal("3.0")
    whale_multiplier: Decimal = Decimal("6.0")
    # Initial calibration, not final — same "needs recalibration with real
    # data" caveat docs/use-cases.md already documents for the other four.
    # 15 minutes of accumulated flow at ~3.3x whale_min: sustained flow is
    # meant to require materially more cumulative dollars than a single
    # whale spike, not just one big period.
    sustained_flow_min: Decimal = Decimal("500000.0")


@dataclass(frozen=True, slots=True)
class WhaleAlert:
    symbol: str
    occ_symbol: str
    alert_type: WhaleAlertType
    amount: Decimal
    as_of: datetime
    # Bulk Volume Classification (Easley, López de Prado, O'Hara 2012)
    # estimates derived from price movement alone — never a measurement of
    # confirmed buy/sell-side order flow. See calculate_bvc.py.
    estimated_buy_volume: Decimal
    estimated_sell_volume: Decimal
    # True when estimated_buy_volume == estimated_sell_volume happened
    # because Lee-Ready had no bid/ask to classify against (Side.UNKNOWN,
    # calculate_lee_ready.py), not because of a genuinely tied split --
    # always False for process()/BVC-derived alerts (that path has no
    # "missing quote" concept; see _ContractState.bucket_quote_unavailable's
    # own comment). The frontend must show a distinct label ("Sin
    # cotización") for this, reserving "Mixto" for an exact split that
    # wasn't caused by missing quote data.
    quote_unavailable: bool = False
    # From 2026-10-17 the tag comes from the contract's own |delta| when one is stored (OTM 0.25-0.40, ATM 0.40-0.60, ITM 0.60-0.85, FAR
    # outside 0.25-0.85: _moneyness_from_delta) and only falls back to the strike-vs-ATR-width rule below when it is not. near_gamma_level now means the
    # STRIKE sits at that level (within a tenth of the ATR width), not that the spot is near it.
    # Strike vs. spot at the moment this alert fired -- see
    # _classify_moneyness's own docstring for why this matters: a deep-ITM
    # call behaves like the underlying itself, not a leveraged directional
    # bet, so it shouldn't read the same as an ATM/OTM alert of the same
    # dollar size. Added 2026-09-28 alongside near_gamma_level/repeat_count
    # so the panel can say something more useful than a raw premium.
    moneyness: Moneyness = Moneyness.ATM
    # Call Wall / Put Wall / Gamma Flip, whichever spot was within
    # NEAR_GAMMA_LEVEL_BAND_PCT of when this alert fired -- None if spot
    # wasn't near any of them. Ties this alert to the same levels the
    # chart already computes, instead of two panels the reader has to
    # reconcile by eye.
    near_gamma_level: str | None = None
    # How many WHALE/UNUSUAL/SUSTAINED_FLOW alerts this exact contract
    # (occ_symbol) has produced so far this session, this one included --
    # 1 the first time, 2 the second, etc. Flags real accumulation at one
    # strike (repeat_count climbing) versus an isolated one-off print,
    # which a bare list of dollar amounts doesn't distinguish at a glance.
    repeat_count: int = 1
    # Premium (USD, price x size x 100) of the trades behind this alert, split
    # by OPRA trade-condition code as str(code) (e.g. {"18": 812000.0, "130":
    # 640000.0}); "-1" = the message carried no condition. For WHALE/UNUSUAL
    # it covers the one-minute bucket, for SUSTAINED_FLOW the same 15 minutes
    # as `amount`, so the values add up to `amount`. CAPTURE ONLY (2026-10):
    # nothing signs, filters or weights by it yet. None for BVC-derived
    # alerts, when capture is switched off, and for rows written before it.
    condition_premium: dict[str, Decimal] | None = None
    # TRIAL second label (2026-10-16, design 12.14): the alert's buy/sell premium counting ONLY trades whose condition is in
    # SIGNED_CONDITIONS, as {"buy": ..., "sell": ...} (a signed trade whose side is unknown splits half and half, like the
    # main figures). Premium of every other condition is left out, so amount - buy - sell is what stays "no side". The main
    # estimated_buy_volume / estimated_sell_volume are NOT changed. Same coverage as condition_premium, same kill switch;
    # None for BVC-derived alerts, when capture is off, and for rows written before it.
    simple_flow: dict[str, Decimal] | None = None
    # What the alert cubes on the chart need (migration 0046): the contract's |delta| when the alert fired (None when no delta was stored) and
    # the contracts traded in the alert's window (same window as `amount`; every trade counts, whatever its condition). None for BVC-derived
    # alerts and for rows written before the migration. delta-equivalent size of a cube = contracts x delta.
    delta: Decimal | None = None
    contracts: int | None = None


@dataclass(frozen=True, slots=True)
class SymbolFlowPressure:
    """Net CLIENT (aggressor) options premium flow for one underlying,
    classified by Lee-Ready (process_trade) -- never a confirmed reading
    of dealer positioning. A trade's aggressor side tells us whether the
    *customer* bought or sold that contract; whether the dealer on the
    other side is opening or closing a position, and therefore what this
    implies about dealer gamma, is not observable from this alone. Field
    names say "client", not "dealer", deliberately, so this isn't
    mistaken for the latter downstream.

    Deliberately fed only by process_trade() (Lee-Ready, the real trade
    stream), never by process() (BVC, periodic chain-snapshot polling)
    even though both eventually classify buy/sell volume -- confirmed
    live, 2026-09: OptionContract.volume (what process()'s volume-delta
    BVC classification is derived from) already comes from
    ThetaTradeStream.cumulative_volume, the *same* trade-stream counter
    process_trade() classifies directly and more precisely, trade by
    trade rather than via a coarser volume-delta proxy. Accumulating
    both here would double-count the same real trades under two
    different classifiers. If the real trade stream isn't connected
    (MockDataProvider, or a real one that never connects), this stays
    empty rather than silently substituting the coarser BVC reading --
    the same "empty and honest, not a stand-in" convention this codebase
    already applies elsewhere (e.g. DerivedMetricValue.provisional).
    """

    symbol: str
    as_of: datetime
    net_call_premium: Decimal
    net_put_premium: Decimal
    net_client_flow_pressure: Decimal
    rolling_net_call_premium: Decimal
    rolling_net_put_premium: Decimal
    rolling_net_client_flow_pressure: Decimal
    rolling_window_minutes: int


def _contract_type_from_occ_symbol(occ_symbol: str) -> ContractType:
    """OCC symbols this codebase builds (_build_occ_symbol in the
    ThetaData adapter) always end in <YYMMDD><C or P><8-digit strike> --
    15 trailing characters regardless of root length, so the call/put
    character is always exactly 9 characters from the end."""
    return ContractType.CALL if occ_symbol[-9] == "C" else ContractType.PUT


def _parse_strike(occ_symbol: str) -> Decimal:
    """Same fixed trailing-shape guarantee _contract_type_from_occ_symbol's
    own comment documents -- the last 8 characters are always the strike,
    x1000 (e.g. "07685000" -> 7685)."""
    return Decimal(occ_symbol[-8:]) / 1000


def _floor_to_minute(moment: datetime) -> datetime:
    return moment.replace(second=0, microsecond=0)


_ZERO = Decimal(0)

# Trade-condition codes whose buy/sell side is trusted for the second ("simple only") label of an alert: 18 auto-execution,
# 95 intermarket sweep, 125/126 single-leg auction. Multi-leg (130-144), floor (129), crosses (127/128), cancels... stay
# unsigned there. TRIAL: the main label keeps signing every trade (see WhaleAlert.simple_flow).
SIGNED_CONDITIONS: frozenset[int] = frozenset({18, 95, 125, 126})


def _merge_simple_flow(buckets: Iterable[tuple[Decimal, Decimal]]) -> tuple[Decimal, Decimal]:
    """Sum of per-bucket (buy, sell) premium pairs of the signed-conditions-only split (SUSTAINED_FLOW's 15 minutes)."""
    buy = sell = _ZERO
    for bucket_buy, bucket_sell in buckets:
        buy += bucket_buy
        sell += bucket_sell
    return buy, sell


def _merge_condition_premium(buckets: Iterable[dict[int, Decimal]]) -> dict[int, Decimal]:
    """Sum of per-bucket {condition code: premium} maps (SUSTAINED_FLOW's 15 minutes)."""
    merged: dict[int, Decimal] = {}
    for bucket in buckets:
        for code, value in bucket.items():
            merged[code] = merged.get(code, _ZERO) + value
    return merged


@dataclass(slots=True)
class _ContractState:
    cumulative_volume: int
    bucket_start: datetime
    # The underlying this contract belongs to -- needed by
    # flush_stale_buckets() to find one symbol's own contracts within
    # _trade_states without re-deriving it from occ_symbol (which, for
    # SPX/NDX/VIX, is built from the raw weekly contract root, not the
    # logical symbol -- see _underlying_symbol_for_root() in the
    # ThetaData adapter). Both process() and process_trade() already
    # know their own symbol at state-construction time, so this is just
    # captured once rather than reconstructed later.
    symbol: str = ""
    bucket_amount: Decimal = Decimal(0)
    previous_amounts: deque[Decimal] = field(default_factory=lambda: deque(maxlen=5))
    sustained_amounts: deque[Decimal] = field(default_factory=lambda: deque(maxlen=15))
    sustained_alerted: bool = False
    # BVC: price history and the rolling volatility window are tracked
    # per raw reading (not per finalized minute) — each reading gets its
    # own buy/sell classification, accumulated into the bucket alongside
    # bucket_amount, same as the sustained-flow windows mirror the
    # whale/unusual one.
    #
    # Windowed by elapsed time (last `_PRICE_VOLATILITY_WINDOW`), not by
    # reading count — a fixed reading-count window (the original design)
    # implicitly assumed a roughly steady polling cadence: 20 readings at
    # ~30s each is ~10 real minutes, a reasonable volatility sample. That
    # assumption breaks once readings can arrive at irregular intervals
    # (a push/streaming provider, or simply a faster/slower poll cadence)
    # — 20 readings could then span 2 seconds during a burst or 20 minutes
    # during a lull, changing what sigma actually measures without the
    # code noticing. Anchoring to real elapsed time instead keeps the
    # window's meaning constant regardless of how often readings arrive —
    # same reasoning already applied to `previous_amounts`/
    # `sustained_amounts` below, which are anchored to finalized calendar
    # minutes rather than a raw reading count.
    previous_price: Decimal | None = None
    price_deltas: deque[tuple[datetime, Decimal]] = field(default_factory=deque)
    bucket_buy_volume: Decimal = Decimal(0)
    bucket_sell_volume: Decimal = Decimal(0)
    sustained_buy_volumes: deque[Decimal] = field(default_factory=lambda: deque(maxlen=15))
    sustained_sell_volumes: deque[Decimal] = field(default_factory=lambda: deque(maxlen=15))
    # Lee-Ready only (process_trade, tracked in a separate _trade_states
    # dict — see WhaleAlertsEngine.__init__) — the tick rule's "carry
    # forward on a zero tick" memory. process()/BVC never reads or writes
    # this field, so this addition changes nothing about that path.
    previous_side: Side = Side.UNKNOWN
    # Lee-Ready only, same non-contamination guarantee as previous_side
    # above (process()/BVC never reads or writes either field, so both
    # stay at their class default -- False/empty -- for every BVC-derived
    # alert). True exactly when every trade that has contributed to the
    # CURRENT in-progress bucket was Side.UNKNOWN (no Quote Stream bid/ask
    # yet for this contract) rather than a real BUY/SELL classification --
    # an AND-accumulation, reset to True (vacuously, nothing to
    # contradict it yet) whenever a bucket rolls over. Confirmed live,
    # 2026-09 (real SPX 0DTE, market open): most of a contract's alerts
    # showing an exact 50/50 estimated_buy_volume/estimated_sell_volume
    # split were this -- a missing quote, not a genuine tied market --
    # process_trade()'s own neutral-split fallback made the two
    # indistinguishable in the stored WhaleAlert until this field.
    bucket_quote_unavailable: bool = False
    # Same idea across the 15-bucket Sustained Flow window -- True only
    # if every one of those buckets was itself quote_unavailable.
    sustained_quote_unavailable: deque[bool] = field(default_factory=lambda: deque(maxlen=15))
    # See WhaleAlert.repeat_count's own docstring -- incremented once per
    # alert _emit() actually produces for this contract, session-lifetime
    # (this state itself only lives as long as the engine process does,
    # so "this session" in practice means "since this process started").
    alert_count: int = 0
    # Lee-Ready only (process_trade): premium of the current bucket by OPRA
    # condition code (-1 = none sent), and the last 15 finalized buckets' maps
    # for SUSTAINED_FLOW. Left empty when condition capture is off and for
    # process()/BVC, which never writes them.
    bucket_condition_premium: dict[int, Decimal] = field(default_factory=dict)
    sustained_condition_premium: deque[dict[int, Decimal]] = field(default_factory=lambda: deque(maxlen=15))
    # Same lifetime as bucket_condition_premium: [buy, sell] premium of the current bucket from SIGNED_CONDITIONS trades only,
    # and the last 15 finalized buckets' pairs.
    bucket_contracts: int = 0
    sustained_contracts: deque[int] = field(default_factory=lambda: deque(maxlen=15))
    bucket_simple_flow: list[Decimal] = field(default_factory=lambda: [_ZERO, _ZERO])
    sustained_simple_flow: deque[tuple[Decimal, Decimal]] = field(default_factory=lambda: deque(maxlen=15))


@dataclass(slots=True)
class _SymbolFlowState:
    """Per-underlying (not per-contract) net client flow — see
    SymbolFlowPressure for the full methodology caveat. `rolling_flow`
    holds one (as_of, call_net, put_net) entry per process_trade() call
    for this symbol, trimmed to the trailing window as new entries
    arrive; `rolling_call_sum`/`rolling_put_sum` are maintained
    incrementally alongside it (updated on both append and trim) so
    reading the current rolling totals is O(1), not a fresh sum over the
    deque on every read."""

    session_date: date | None = None
    net_call_premium: Decimal = Decimal(0)
    net_put_premium: Decimal = Decimal(0)
    rolling_flow: deque[tuple[datetime, Decimal, Decimal]] = field(default_factory=deque)
    rolling_call_sum: Decimal = Decimal(0)
    rolling_put_sum: Decimal = Decimal(0)
    last_as_of: datetime | None = None


class WhaleAlertsEngine:
    """Detect unusual contract volume from provider-independent chain snapshots.

    Each call to `process()` carries one raw reading (today, roughly every
    30s, whenever the internal trigger fires — the engine itself has no
    fixed cadence). Readings are grouped into real calendar-minute buckets
    using `chain.as_of` (same floor-to-minute approach the frontend already
    uses for candles), and only a *finalized* 1-minute bucket is ever
    classified or windowed — never a raw sub-minute reading.

    Thresholds are read from `storage`, cached for `_THRESHOLDS_CACHE_TTL_SECONDS`
    at a time -- an edit made through the thresholds endpoint takes effect
    within that window, not on the very next trigger, and never requires a
    restart. Was an uncached read on every single call until 2026-09-11:
    confirmed live (real Postgres, real concurrent load shaped like
    production's ~15 whale-alerts consumer threads) that this query was
    ~85-95% of process_trade()'s own per-trade cost, and under contention
    for the shared connection pool its tail latency spiked to ~870ms on a
    single call -- enough, on its own, to stall a busy symbol's entire
    consumption for that long with nothing draining its queue. A five-
    second TTL cuts real per-trade query volume by 2+ orders of magnitude
    at the trade rates seen during that incident, while keeping threshold
    edits' turnaround indistinguishable from "immediate" for the human
    operator making them. Only the thresholds are cached this way;
    `_states`/`_alerts` (the per-contract windowing memory and alert
    history) stay exactly as long-lived, in-memory engine state --
    re-fetching those per call would defeat the whole windowing mechanism.
    """

    _WINDOW_SIZE = 5
    _SUSTAINED_WINDOW_SIZE = 15
    _CONTRACT_MULTIPLIER = Decimal(100)
    # See this class's own docstring for the live measurements behind this
    # number. process_trade() is called concurrently from many different
    # executor threads (one per symbol), so the cache itself must be
    # thread-safe -- see _thresholds_cache_lock below.
    _THRESHOLDS_CACHE_TTL_SECONDS = 5.0
    # 10 minutes: same order of magnitude the old 20-reading window
    # represented at the ~30s polling cadence it was designed around, but
    # anchored to real elapsed time so it holds regardless of how often
    # readings actually arrive (see `_ContractState.price_deltas`).
    _PRICE_VOLATILITY_WINDOW = timedelta(minutes=10)
    # Net client flow pressure (SymbolFlowPressure): same order of
    # magnitude as _PRICE_VOLATILITY_WINDOW (10min) and the per-contract
    # Sustained Flow window (15 readings) above -- picked to sit in the
    # same ballpark as this file's other time-based windows, not a new,
    # unrelated magnitude. Answers "is something concentrated happening
    # right now", alongside net_call_premium/net_put_premium's
    # session-since-open total, which answers "what's today's bias so
    # far" -- genuinely different questions, so both are kept rather than
    # picking one.
    _NET_FLOW_ROLLING_WINDOW = timedelta(minutes=15)

    def __init__(
        self,
        storage: IStorage,
        default_thresholds: WhaleAlertThresholds | None = None,
        alert_limit: int = 1000,
        thresholds_cache_ttl_seconds: float = _THRESHOLDS_CACHE_TTL_SECONDS,
        bvc_alerts_enabled: bool = True,
        store_conditions: bool = True,
    ) -> None:
        self._storage = storage
        # True: process_trade() also tallies each bucket's premium by OPRA
        # trade-condition code and _emit() stores it on the alert. Capture
        # only -- see WhaleAlert.condition_premium. Settings.
        # whale_alerts_store_conditions is the kill switch.
        self._store_conditions = store_conditions
        # False turns process() (BVC, fed by the REST scheduler's chain
        # snapshots) into a no-op. With a live trade stream process_trade()
        # (Lee-Ready) already covers every streamed contract: BVC alerts
        # there duplicated that flow, disagreed with its direction (~45%
        # same sign) and fired on volume-counter artifacts. See
        # Settings.whale_alerts_bvc_active for how production sets this.
        self._bvc_alerts_enabled = bvc_alerts_enabled
        self._default_thresholds = default_thresholds or WhaleAlertThresholds()
        self._thresholds_cache_ttl_seconds = thresholds_cache_ttl_seconds
        self._thresholds_cache: dict[str, WhaleThreshold] | None = None
        self._thresholds_cache_at: float = 0.0
        # Guards both the staleness check and the refresh itself -- a
        # cache miss under this lock means only one of the ~15 concurrent
        # symbol threads actually queries Postgres when the TTL expires,
        # the rest see the freshly-populated cache instead of each firing
        # their own redundant query at the same moment.
        self._thresholds_cache_lock = threading.Lock()
        # Same cache-with-TTL shape as _cached_thresholds above, and for
        # the same reason -- a per-trade Postgres round-trip (get_latest_
        # price + get_latest_gamma_aggregate) would repeat that method's
        # own documented incident (query cost dominating process_trade()'s
        # own per-trade cost under concurrent load). Reuses the same TTL;
        # moneyness/near_gamma_level are context tags, not the alert's own
        # trigger condition, so a few seconds of staleness on spot/gamma
        # levels is an acceptable tradeoff for not re-querying on every
        # single trade across ~15 concurrent symbol threads.
        self._market_context_cache: dict[str, tuple[float, tuple[Decimal | None, GammaAggregate | None]]] = {}
        self._market_context_cache_lock = threading.Lock()
        # occ_symbol -> (monotonic time, |delta| or None): the contract's delta is read once per fired alert and kept for a minute
        # (an alert repeats on the same strikes), see _cached_option_delta.
        self._option_delta_cache: dict[str, tuple[float, Decimal | None]] = {}
        self._option_delta_cache_lock = threading.Lock()
        self._states: dict[str, _ContractState] = {}
        # Separate from _states (process()/BVC) on purpose — process_trade()
        # (Lee-Ready) keeps its own per-contract bucketing state so the two
        # mechanisms can run concurrently under ThetaDataProvider (which
        # still polls for greeks/IV/OI while also streaming trades) without
        # either one's volume ever being double-counted into the other's
        # bucket. See _ContractState and process_trade().
        self._trade_states: dict[str, _ContractState] = {}
        self._alerts: deque[WhaleAlert] = deque(maxlen=alert_limit)
        # Per-underlying (not per-contract) net client flow -- see
        # SymbolFlowPressure/_SymbolFlowState. Fed only by process_trade()
        # (see that method's own comment for why not process() too).
        self._symbol_flow: dict[str, _SymbolFlowState] = {}

    def _cached_thresholds(self) -> dict[str, WhaleThreshold]:
        """See this class's own docstring for why this is cached (and why
        with a lock) rather than querying `storage` on every trade."""
        with self._thresholds_cache_lock:
            now = time.monotonic()
            if (
                self._thresholds_cache is None
                or now - self._thresholds_cache_at >= self._thresholds_cache_ttl_seconds
            ):
                self._thresholds_cache = self._storage.get_whale_thresholds()
                self._thresholds_cache_at = now
            return self._thresholds_cache

    def _resolve_thresholds(self, symbol: str) -> WhaleAlertThresholds:
        persisted = self._cached_thresholds().get(symbol.upper())
        if persisted is None:
            return self._default_thresholds
        return WhaleAlertThresholds(
            unusual_min=persisted.unusual_min,
            whale_min=persisted.whale_min,
            unusual_multiplier=persisted.unusual_multiplier,
            whale_multiplier=persisted.whale_multiplier,
            sustained_flow_min=persisted.sustained_flow_min,
        )

    def _cached_market_context(self, symbol: str) -> tuple[Decimal | None, GammaAggregate | None]:
        """(latest spot price, latest Tactical GammaAggregate) for
        `symbol`, cached -- see this class's own __init__ comment on
        _market_context_cache for why. Tactical (0-2 DTE) since the
        Structural view is being retired (2026-10-15, the user never looks
        at it): these are the same Call Wall/Put Wall/Gamma Flip the chart
        shows. Tactical can be legitimately empty on a day/symbol with no
        0-2 DTE contracts (see GammaAggregate.view's own docstring); the
        callers already treat a missing level as "no tag"."""
        with self._market_context_cache_lock:
            now = time.monotonic()
            cached = self._market_context_cache.get(symbol)
            if cached is not None and now - cached[0] < self._thresholds_cache_ttl_seconds:
                return cached[1]
            price = self._storage.get_latest_price(symbol)
            gamma = self._storage.get_latest_gamma_aggregate(symbol, view="tactical")
            context = (price.price if price is not None else None, gamma)
            self._market_context_cache[symbol] = (now, context)
            return context

    _OPTION_DELTA_CACHE_TTL_SECONDS = 60.0

    def _cached_option_delta(self, occ_symbol: str) -> Decimal | None:
        """The contract's delta from its latest stored chain snapshot, cached for a minute. Called only when an alert FIRES (never per
        trade), so the one indexed query it costs is not on the trade path."""
        with self._option_delta_cache_lock:
            now = time.monotonic()
            cached = self._option_delta_cache.get(occ_symbol)
            if cached is not None and now - cached[0] < self._OPTION_DELTA_CACHE_TTL_SECONDS:
                return cached[1]
            if len(self._option_delta_cache) > 5000:
                self._option_delta_cache.clear()
        try:
            delta = self._storage.get_latest_option_delta(occ_symbol)
        except Exception:  # a context tag must never stop an alert from being emitted
            logger.warning("Could not read the delta of %s for its moneyness tag", occ_symbol, exc_info=True)
            delta = None
        with self._option_delta_cache_lock:
            self._option_delta_cache[occ_symbol] = (now, delta)
        return delta

    def process(self, chain: OptionChain) -> tuple[WhaleAlert, ...]:
        if not self._bvc_alerts_enabled:
            return ()
        generated: list[WhaleAlert] = []
        thresholds = self._resolve_thresholds(chain.symbol)
        current_bucket_start = _floor_to_minute(chain.as_of)

        for contract in chain.contracts:
            state = self._states.get(contract.occ_symbol)
            if state is None:
                self._states[contract.occ_symbol] = _ContractState(
                    cumulative_volume=contract.volume,
                    bucket_start=current_bucket_start,
                    symbol=chain.symbol,
                    previous_price=contract.last,
                )
                continue

            delta = contract.volume - state.cumulative_volume
            state.cumulative_volume = contract.volume
            if delta < 0:
                # Session rollover — the volume counter is no longer
                # comparable to anything accumulated so far, so every
                # window (whale/unusual, sustained flow, BVC price
                # history, the in-progress bucket) is discarded, same
                # treatment the original code already gave
                # `previous_amounts`.
                state.previous_amounts.clear()
                state.sustained_amounts.clear()
                state.sustained_alerted = False
                state.bucket_amount = Decimal(0)
                state.bucket_start = current_bucket_start
                state.previous_price = contract.last
                state.price_deltas.clear()
                state.bucket_buy_volume = Decimal(0)
                state.bucket_sell_volume = Decimal(0)
                state.sustained_buy_volumes.clear()
                state.sustained_sell_volumes.clear()
                continue

            amount = Decimal(delta) * contract.last * self._CONTRACT_MULTIPLIER

            # BVC: classified per raw reading (not per finalized minute),
            # using the reading's own price change against the current
            # rolling volatility window, then accumulated into the
            # in-progress bucket alongside `amount` — see calculate_bvc.py.
            price_delta = contract.last - state.previous_price
            state.price_deltas.append((chain.as_of, price_delta))
            cutoff = chain.as_of - self._PRICE_VOLATILITY_WINDOW
            while state.price_deltas and state.price_deltas[0][0] < cutoff:
                state.price_deltas.popleft()
            sigma = calculate_price_volatility([delta for _, delta in state.price_deltas])
            buy_volume, sell_volume = calculate_bvc_split(price_delta, sigma, Decimal(delta))
            state.previous_price = contract.last

            if current_bucket_start != state.bucket_start:
                generated.extend(
                    self._finalize_bucket(
                        state,
                        chain.symbol,
                        contract.occ_symbol,
                        chain.as_of,
                        thresholds,
                        current_bucket_start,
                    )
                )

            state.bucket_amount += amount
            state.bucket_buy_volume += buy_volume
            state.bucket_sell_volume += sell_volume

        return tuple(generated)

    def process_trade(self, event: FlowEvent, quote: LatestQuote | None) -> tuple[WhaleAlert, ...]:
        """Classify one individual trade with Lee-Ready (1991) and feed it
        into the same per-minute bucketing / Sustained Flow mechanism
        process() already uses — see calculate_lee_ready.py for the
        classification itself, and _ContractState/__init__ for why this
        keeps its own separate state (_trade_states, not _states).

        `quote` is whatever the caller (StreamWhaleAlertsUseCase) last saw
        on the Quote Stream for this contract — `None` means no quote has
        arrived yet (e.g. right at startup), which classify_trade_side
        already treats as a documented neutral case, not a guess.

        Known limitation, deliberately out of scope here: unlike process(),
        this has no volume-counter-based session-rollover detection (a
        continuous trade stream has no equivalent counter to watch) — the
        5-minute/15-minute rolling windows are not explicitly cleared
        across a session boundary. Swapping the classification mechanism
        was this change's only goal; rollover handling for the streaming
        path is a separate concern to revisit later.

        Gated on is_market_open, same reasoning and same gate
        StreamUnderlyingPriceUseCase._maybe_persist already uses: a real
        pre/post-market options trade still arrives over ThetaData's
        stream, but on much thinner, wider-spread liquidity than the
        regular session -- confirmed live, 2026-09-29, real SPX 0DTE
        SUSTAINED_FLOW/UNUSUAL alerts (real dollar amounts, $800K-$937K)
        firing at 7:50am ET, an hour and forty minutes before the open.
        Unlike process() (only ever called by the scheduler, which
        already never runs a cycle outside market hours), this streaming
        path has no other gate upstream of it. A dropped extended-hours
        trade contributes nothing at all here -- not to the bucket, not
        to symbol flow, not to the Lee-Ready tick-rule memory -- so the
        first real trade of the regular session compares against the
        previous regular session's own last price, not a premarket print.
        """
        if not is_market_open(event.as_of):
            return ()
        thresholds = self._resolve_thresholds(event.symbol)
        current_bucket_start = _floor_to_minute(event.as_of)

        state = self._trade_states.get(event.occ_symbol)
        if state is None:
            # bucket_quote_unavailable=True: vacuously, for the same
            # reason _finalize_bucket resets it to True on every rollover
            # -- nothing has contributed to this brand-new bucket yet to
            # disprove it. Only process_trade() ever passes this kwarg;
            # process()'s own _ContractState construction leaves it at
            # its class default (False, inert for that path).
            state = _ContractState(
                cumulative_volume=0,
                bucket_start=current_bucket_start,
                symbol=event.symbol,
                bucket_quote_unavailable=True,
            )
            self._trade_states[event.occ_symbol] = state

        # FlowEvent carries `premium` (price × size × 100), not the raw
        # per-contract trade price the quote rule/tick rule need directly.
        # Recovered by undoing that exact multiplication — exact under
        # Decimal, no rounding introduced — rather than adding a
        # price-specific field to FlowEvent, which is also reconstructed
        # from a persisted schema with no such column (postgresql.py).
        price = event.premium / (Decimal(event.size) * self._CONTRACT_MULTIPLIER)
        bid = quote.bid if quote is not None else None
        ask = quote.ask if quote is not None else None
        side = classify_trade_side(price, bid, ask, state.previous_price, state.previous_side)
        state.previous_price = price
        state.previous_side = side

        if side is Side.BUY:
            buy_volume, sell_volume = event.premium, Decimal(0)
        elif side is Side.SELL:
            buy_volume, sell_volume = Decimal(0), event.premium
        else:
            half = event.premium / 2
            buy_volume, sell_volume = half, half

        self._record_symbol_flow(
            event.symbol,
            event.as_of,
            _contract_type_from_occ_symbol(event.occ_symbol),
            buy_volume - sell_volume,
        )

        generated: list[WhaleAlert] = []
        if current_bucket_start != state.bucket_start:
            generated.extend(
                self._finalize_bucket(
                    state,
                    event.symbol,
                    event.occ_symbol,
                    event.as_of,
                    thresholds,
                    current_bucket_start,
                )
            )

        state.bucket_amount += event.premium
        state.bucket_contracts += event.size
        state.bucket_buy_volume += buy_volume
        state.bucket_sell_volume += sell_volume
        if self._store_conditions:
            code = -1 if event.condition is None else event.condition
            tally = state.bucket_condition_premium
            tally[code] = tally.get(code, _ZERO) + event.premium
            if code in SIGNED_CONDITIONS:
                state.bucket_simple_flow[0] += buy_volume
                state.bucket_simple_flow[1] += sell_volume
        # AND-accumulation, not assignment: stays True only if EVERY
        # trade contributing to this bucket (including ones already
        # folded in before this call) was Side.UNKNOWN. One real BUY/SELL
        # trade in an otherwise-UNKNOWN bucket correctly flips this to
        # False for the whole bucket -- an exact tie in that mixed case
        # would be a genuine coincidence worth calling "Mixto", not
        # "Sin cotización".
        state.bucket_quote_unavailable = state.bucket_quote_unavailable and side is Side.UNKNOWN
        return tuple(generated)

    def process_trade_batch(
        self, events: list[tuple[FlowEvent, LatestQuote | None]]
    ) -> tuple[WhaleAlert, ...]:
        """Same per-trade classification/bucketing as calling process_trade()
        once per (event, quote) pair in order -- this batches the CALLER's
        executor submission (see StreamWhaleAlertsUseCase's own docstring
        for why SPY specifically needs this), not the classification
        algorithm itself, so results are identical to the unbatched path."""
        generated: list[WhaleAlert] = []
        for event, quote in events:
            generated.extend(self.process_trade(event, quote))
        return tuple(generated)

    def flush_stale_buckets(self, symbol: str, now: datetime) -> tuple[WhaleAlert, ...]:
        """Force-closes `symbol`'s own in-progress Lee-Ready buckets
        (_trade_states only -- process()'s own _states never need this,
        since every reading cycle re-visits every contract in the chain
        regardless of whether it traded, so _finalize_bucket already runs
        on time there via chain.as_of alone) whose calendar minute has
        fully elapsed, even though no new trade has arrived for that
        specific contract to trigger the close via process_trade()'s own
        `current_bucket_start != state.bucket_start` check.

        Found 2026-09-25: a thinly-traded contract's bucket (and any
        Whale/Unusual alert it would produce) previously wouldn't close
        until that SAME contract's next trade -- multi-minute delays, or
        never, for anything that doesn't trade again that session.
        `process()` was never affected (see above), so this is specific
        to the streaming path.

        Called from this symbol's own single-threaded stream consumer
        (StreamWhaleAlertsUseCase.run(), between trade-processing calls,
        guarded by the same per-symbol asyncio.Lock used there) -- never
        concurrently with process_trade() for this symbol's own
        contracts, so no additional locking is needed here. Other
        symbols' contracts are simply skipped (not locked out), same as
        process_trade() itself never touching another symbol's state.
        """
        normalized = symbol.upper()
        thresholds = self._resolve_thresholds(normalized)
        current_bucket_start = _floor_to_minute(now)
        generated: list[WhaleAlert] = []
        for occ_symbol, state in list(self._trade_states.items()):
            if state.symbol.upper() != normalized or state.bucket_start >= current_bucket_start:
                continue
            generated.extend(
                self._finalize_bucket(
                    state, normalized, occ_symbol, now, thresholds, current_bucket_start
                )
            )
        return tuple(generated)

    def _finalize_bucket(
        self,
        state: _ContractState,
        symbol: str,
        occ_symbol: str,
        as_of: datetime,
        thresholds: WhaleAlertThresholds,
        new_bucket_start: datetime,
    ) -> list[WhaleAlert]:
        """Close `state`'s in-progress bucket: classify it against the
        trailing 5-minute average, roll it into the 15-minute Sustained
        Flow window, then reset the bucket for `new_bucket_start`. Shared
        by process() and process_trade() — identical bucketing/alerting
        rules regardless of which classifier (BVC or Lee-Ready) produced
        the bucket's buy/sell split.
        """
        generated: list[WhaleAlert] = []
        finalized_amount = state.bucket_amount
        finalized_buy_volume = state.bucket_buy_volume
        finalized_sell_volume = state.bucket_sell_volume
        finalized_quote_unavailable = state.bucket_quote_unavailable
        finalized_condition_premium = state.bucket_condition_premium
        finalized_simple_flow = (state.bucket_simple_flow[0], state.bucket_simple_flow[1])
        finalized_contracts = state.bucket_contracts

        if len(state.previous_amounts) == self._WINDOW_SIZE:
            average_amount = sum(state.previous_amounts, Decimal()) / self._WINDOW_SIZE
            alert_type = self._classify(finalized_amount, average_amount, thresholds)
            if alert_type is not None:
                generated.append(
                    self._emit(
                        state,
                        symbol,
                        occ_symbol,
                        as_of,
                        alert_type,
                        finalized_amount,
                        finalized_buy_volume,
                        finalized_sell_volume,
                        finalized_quote_unavailable,
                        finalized_condition_premium,
                        finalized_simple_flow,
                        finalized_contracts,
                    )
                )

        state.sustained_amounts.append(finalized_amount)
        state.sustained_buy_volumes.append(finalized_buy_volume)
        state.sustained_sell_volumes.append(finalized_sell_volume)
        state.sustained_quote_unavailable.append(finalized_quote_unavailable)
        state.sustained_condition_premium.append(finalized_condition_premium)
        state.sustained_simple_flow.append(finalized_simple_flow)
        state.sustained_contracts.append(finalized_contracts)
        if len(state.sustained_amounts) == self._SUSTAINED_WINDOW_SIZE:
            sustained_total = sum(state.sustained_amounts, Decimal())
            if sustained_total >= thresholds.sustained_flow_min:
                if not state.sustained_alerted:
                    state.sustained_alerted = True
                    generated.append(
                        self._emit(
                            state,
                            symbol,
                            occ_symbol,
                            as_of,
                            WhaleAlertType.SUSTAINED_FLOW,
                            sustained_total,
                            sum(state.sustained_buy_volumes, Decimal()),
                            sum(state.sustained_sell_volumes, Decimal()),
                            all(state.sustained_quote_unavailable),
                            _merge_condition_premium(state.sustained_condition_premium),
                            _merge_simple_flow(state.sustained_simple_flow),
                            sum(state.sustained_contracts),
                        )
                    )
            else:
                state.sustained_alerted = False

        state.previous_amounts.append(finalized_amount)
        state.bucket_start = new_bucket_start
        state.bucket_amount = Decimal(0)
        state.bucket_buy_volume = Decimal(0)
        state.bucket_sell_volume = Decimal(0)
        # Vacuously true for the bucket that starts now -- nothing has
        # contributed to it yet to disprove it. process()/BVC resets this
        # too (shared method), but since that path never reads it, it's
        # inert there -- same non-contamination guarantee as the field's
        # own comment.
        state.bucket_quote_unavailable = True
        # A NEW dict, not .clear(): the one just finalized is also held by
        # sustained_condition_premium.
        state.bucket_condition_premium = {}
        state.bucket_simple_flow = [_ZERO, _ZERO]
        state.bucket_contracts = 0
        return generated

    def _emit(
        self,
        state: _ContractState,
        symbol: str,
        occ_symbol: str,
        as_of: datetime,
        alert_type: WhaleAlertType,
        amount: Decimal,
        estimated_buy_volume: Decimal,
        estimated_sell_volume: Decimal,
        quote_unavailable: bool = False,
        condition_premium: dict[int, Decimal] | None = None,
        simple_flow: tuple[Decimal, Decimal] | None = None,
        contracts: int | None = None,
    ) -> WhaleAlert:
        spot, gamma = self._cached_market_context(symbol)
        strike = _parse_strike(occ_symbol)
        contract_type = _contract_type_from_occ_symbol(occ_symbol)
        state.alert_count += 1
        delta = self._cached_option_delta(occ_symbol)
        alert = WhaleAlert(
            symbol=symbol,
            occ_symbol=occ_symbol,
            alert_type=alert_type,
            amount=amount,
            as_of=as_of,
            estimated_buy_volume=estimated_buy_volume,
            estimated_sell_volume=estimated_sell_volume,
            quote_unavailable=quote_unavailable,
            moneyness=_moneyness_from_delta(delta)
            or _classify_moneyness(
                contract_type,
                strike,
                spot,
                gamma.near_the_money_width if gamma is not None else None,
            ),
            near_gamma_level=_nearest_level_to_strike(strike, spot, gamma),
            delta=abs(delta) if delta is not None and delta != 0 else None,
            # Only the Lee-Ready path (process_trade) sees trade sizes; BVC-derived alerts tally none (0 here), which reads as unknown.
            contracts=contracts or None,
            repeat_count=state.alert_count,
            condition_premium=(
                {str(code): value for code, value in condition_premium.items()} if condition_premium else None
            ),
            # Present exactly when condition_premium is (both come from the same capture switch).
            simple_flow=(
                {"buy": simple_flow[0], "sell": simple_flow[1]} if simple_flow is not None and condition_premium else None
            ),
        )
        self._alerts.append(alert)
        # Dual-write, this phase only: whale_alerts now persists the same
        # alert this in-memory deque already held (see IStorage's own
        # docstring) -- /alerts and the screener still read _alerts, not
        # Postgres, until that migration is separately approved. Both
        # paths funnel through this one method (process() and
        # process_trade() both call _finalize_bucket, which only ever
        # calls _emit here), so this is the single place that needs it.
        self._storage.save_whale_alert(alert)
        return alert

    def recent_alerts(self, symbol: str, limit: int = 100) -> tuple[WhaleAlert, ...]:
        normalized = symbol.upper()
        matches = (alert for alert in reversed(self._alerts) if alert.symbol == normalized)
        return tuple(alert for _, alert in zip(range(limit), matches, strict=False))

    def _record_symbol_flow(
        self, symbol: str, as_of: datetime, contract_type: ContractType, net: Decimal
    ) -> None:
        normalized = symbol.upper()
        state = self._symbol_flow.setdefault(normalized, _SymbolFlowState())

        session_date = as_of.astimezone(EASTERN_TIME).date()
        if state.session_date != session_date:
            # New session (or the first reading ever for this symbol) --
            # the whole point of a session-accumulated total is that it
            # resets at the open, same convention as
            # calculate_session_open/isWithinRegularSession elsewhere in
            # this codebase, just enforced here rather than assumed from
            # a gap in incoming events (a Worker restart mid-session
            # would otherwise silently look like a fresh session too --
            # accepted, since a restart already loses this in-memory
            # state entirely regardless).
            state.session_date = session_date
            state.net_call_premium = Decimal(0)
            state.net_put_premium = Decimal(0)
            state.rolling_flow.clear()
            state.rolling_call_sum = Decimal(0)
            state.rolling_put_sum = Decimal(0)

        if contract_type == ContractType.CALL:
            state.net_call_premium += net
            state.rolling_call_sum += net
            state.rolling_flow.append((as_of, net, Decimal(0)))
        else:
            state.net_put_premium += net
            state.rolling_put_sum += net
            state.rolling_flow.append((as_of, Decimal(0), net))

        cutoff = as_of - self._NET_FLOW_ROLLING_WINDOW
        while state.rolling_flow and state.rolling_flow[0][0] < cutoff:
            _, old_call, old_put = state.rolling_flow.popleft()
            state.rolling_call_sum -= old_call
            state.rolling_put_sum -= old_put

        state.last_as_of = as_of

    def symbol_flow(self, symbol: str) -> SymbolFlowPressure | None:
        """Current net client flow pressure for `symbol`, or `None` if no
        trade has been classified for it yet this session (Worker just
        started, or the real trade stream isn't connected -- see
        SymbolFlowPressure's own docstring for why this is left empty
        rather than approximated)."""
        state = self._symbol_flow.get(symbol.upper())
        if state is None or state.last_as_of is None:
            return None
        return SymbolFlowPressure(
            symbol=symbol.upper(),
            as_of=state.last_as_of,
            net_call_premium=state.net_call_premium,
            net_put_premium=state.net_put_premium,
            net_client_flow_pressure=state.net_call_premium - state.net_put_premium,
            rolling_net_call_premium=state.rolling_call_sum,
            rolling_net_put_premium=state.rolling_put_sum,
            rolling_net_client_flow_pressure=state.rolling_call_sum - state.rolling_put_sum,
            rolling_window_minutes=int(self._NET_FLOW_ROLLING_WINDOW.total_seconds() // 60),
        )

    @staticmethod
    def _classify(
        amount: Decimal,
        average_amount: Decimal,
        thresholds: WhaleAlertThresholds,
    ) -> WhaleAlertType | None:
        if (
            amount >= thresholds.whale_min
            and amount > average_amount * thresholds.whale_multiplier
        ):
            return WhaleAlertType.WHALE
        if (
            amount >= thresholds.unusual_min
            and amount > average_amount * thresholds.unusual_multiplier
        ):
            return WhaleAlertType.UNUSUAL
        return None
