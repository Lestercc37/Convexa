from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import StrEnum

SCHEMA_VERSION = 1


class DomainError(ValueError):
    """Base exception for pure domain invariant violations."""


class InvalidStrikeError(DomainError):
    """Raised when an option strike is not strictly positive."""


class InvalidExpirationError(DomainError):
    """Raised when an expiration date violates domain invariants."""


class InvalidOptionError(DomainError):
    """Raised when option contract, quote, or Greek invariants fail."""


class ContractType(StrEnum):
    CALL = "call"
    PUT = "put"


OptionType = ContractType


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"
    UNKNOWN = "unknown"


class MarketState(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    PRE_MARKET = "pre_market"
    AFTER_HOURS = "after_hours"
    UNKNOWN = "unknown"


class UnderlyingKind(StrEnum):
    EQUITY = "equity"
    INDEX = "index"
    FUTURE = "future"


class FlowEventType(StrEnum):
    SWEEP = "sweep"
    BLOCK = "block"
    UNUSUAL = "unusual"


AggressorSide = Side


@dataclass(frozen=True, slots=True)
class Underlying:
    symbol: str
    kind: UnderlyingKind
    is_priority: bool = False

    def __post_init__(self) -> None:
        if not self.symbol or not self.symbol.strip():
            raise InvalidOptionError("underlying symbol is required")
        object.__setattr__(self, "symbol", self.symbol.upper())


@dataclass(frozen=True, slots=True)
class WhaleThreshold:
    symbol: str
    unusual_min: Decimal
    whale_min: Decimal
    unusual_multiplier: Decimal
    whale_multiplier: Decimal
    sustained_flow_min: Decimal

    def __post_init__(self) -> None:
        if not self.symbol or not self.symbol.strip():
            raise InvalidOptionError("whale threshold symbol is required")
        object.__setattr__(self, "symbol", self.symbol.upper())
        for name in (
            "unusual_min",
            "whale_min",
            "unusual_multiplier",
            "whale_multiplier",
            "sustained_flow_min",
        ):
            value = getattr(self, name)
            _ensure_finite_decimal(value, InvalidOptionError, name)
            if value <= 0:
                raise InvalidOptionError(f"{name} must be positive")


class ScreenerPreset(StrEnum):
    UNUSUAL_OPTIONS_ACTIVITY = "unusual-options-activity"
    NEGATIVE_GAMMA_BOARD = "negative-gamma-board"
    MAX_PAIN_KEY_LEVELS = "max-pain-key-levels"
    VANNA_EXPOSURE_LEADERS = "vanna-exposure-leaders"
    CHARM_DECAY_PRESSURE = "charm-decay-pressure"

    @classmethod
    def parse(cls, value: str) -> ScreenerPreset:
        return cls(value.strip().lower().replace("_", "-").replace(" ", "-"))


@dataclass(frozen=True, slots=True)
class NegativeGammaBoardSettings:
    """Editable filter for the Negative Gamma Board preset.

    `net_gamma_max` replaces the value that was hardcoded as `0`
    (`net_gamma < net_gamma_max`) — unlike the two exposure-leader
    thresholds below, this one is never "no filter": the preset is
    inherently about negative gamma, so an unset row falls back to the
    original `0` default, not an unbounded board.
    """

    net_gamma_max: Decimal = Decimal(0)

    def __post_init__(self) -> None:
        _ensure_finite_decimal(self.net_gamma_max, InvalidOptionError, "net_gamma_max")


@dataclass(frozen=True, slots=True)
class ExposureLeadersSettings:
    """Editable filter/limit shared by Vanna Exposure Leaders and Charm
    Decay Pressure — both rank by `|vanna_exposure|`/`|charm_exposure|`
    with no threshold or cap today. Both fields are genuinely optional:
    `None` means "behave exactly as today" (no minimum, no result cap).
    """

    min_magnitude: Decimal | None = None
    limit: int | None = None

    def __post_init__(self) -> None:
        if self.min_magnitude is not None:
            _ensure_finite_decimal(self.min_magnitude, InvalidOptionError, "min_magnitude")
            if self.min_magnitude < 0:
                raise InvalidOptionError("min_magnitude cannot be negative")
        if self.limit is not None and self.limit < 1:
            raise InvalidOptionError("limit must be a positive integer")


ScreenerPresetSettings = NegativeGammaBoardSettings | ExposureLeadersSettings


@dataclass(frozen=True, slots=True)
class OptionGreeks:
    delta: Decimal
    gamma: Decimal
    theta: Decimal
    vega: Decimal
    charm: Decimal
    vanna: Decimal

    def __post_init__(self) -> None:
        _ensure_finite_decimal(self.delta, InvalidOptionError, "delta")
        _ensure_finite_decimal(self.gamma, InvalidOptionError, "gamma")
        if not Decimal("-1") <= self.delta <= Decimal("1"):
            raise InvalidOptionError("delta must be between -1 and 1")
        for name in ("theta", "vega", "charm", "vanna"):
            _ensure_finite_decimal(getattr(self, name), InvalidOptionError, name)


Greeks = OptionGreeks


@dataclass(frozen=True, slots=True)
class Expiration:
    expiration: date
    as_of: date

    def __post_init__(self) -> None:
        if not isinstance(self.expiration, date) or not isinstance(self.as_of, date):
            raise InvalidExpirationError("expiration and as_of must be dates")
        if self.expiration < self.as_of:
            raise InvalidExpirationError("expiration cannot be before as_of")

    @property
    def dte(self) -> int:
        return max((self.expiration - self.as_of).days, 0)


@dataclass(frozen=True, slots=True)
class OptionContract:
    underlying: str
    strike: Decimal
    expiration: date
    contract_type: ContractType
    occ_symbol: str
    bid: Decimal
    ask: Decimal
    last: Decimal
    volume: int
    open_interest: int
    iv: Decimal
    greeks: OptionGreeks

    def __post_init__(self) -> None:
        if not self.underlying or not self.underlying.strip():
            raise InvalidOptionError("underlying is required")
        object.__setattr__(self, "underlying", self.underlying.upper())
        _ensure_positive_decimal(self.strike, InvalidStrikeError, "strike")
        if not isinstance(self.expiration, date):
            raise InvalidExpirationError("expiration must be a date")
        if not self.occ_symbol:
            raise InvalidOptionError("occ_symbol is required")
        for name in ("bid", "ask", "last", "iv"):
            _ensure_finite_decimal(getattr(self, name), InvalidOptionError, name)
        if self.bid < 0 or self.ask < 0 or self.last < 0 or self.iv < 0:
            raise InvalidOptionError("quote monetary values and iv cannot be negative")
        if self.ask < self.bid:
            raise InvalidOptionError("ask cannot be lower than bid")
        if self.volume < 0 or self.open_interest < 0:
            raise InvalidOptionError("volume and open_interest cannot be negative")


@dataclass(frozen=True, slots=True)
class OptionChain:
    symbol: str
    as_of: datetime
    spot_price: Decimal
    contracts: tuple[OptionContract, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.symbol or not self.symbol.strip():
            raise InvalidOptionError("chain symbol is required")
        object.__setattr__(self, "symbol", self.symbol.upper())
        _ensure_positive_decimal(self.spot_price, InvalidOptionError, "spot_price")


@dataclass(frozen=True, slots=True)
class MaxPainStrikePain:
    strike: Decimal
    total_call_pain: Decimal
    total_put_pain: Decimal
    total_pain: Decimal

    def __post_init__(self) -> None:
        _ensure_positive_decimal(self.strike, InvalidStrikeError, "strike")
        for name in ("total_call_pain", "total_put_pain", "total_pain"):
            _ensure_finite_decimal(getattr(self, name), InvalidOptionError, name)
            if getattr(self, name) < 0:
                raise InvalidOptionError(f"{name} cannot be negative")


@dataclass(frozen=True, slots=True)
class MaxPain:
    symbol: str
    as_of: datetime
    max_pain_strike: Decimal
    total_call_pain: Decimal
    total_put_pain: Decimal
    total_pain: Decimal
    ranking: tuple[MaxPainStrikePain, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.symbol or not self.symbol.strip():
            raise InvalidOptionError("max pain symbol is required")
        object.__setattr__(self, "symbol", self.symbol.upper())
        if self.max_pain_strike:
            _ensure_positive_decimal(self.max_pain_strike, InvalidStrikeError, "max_pain_strike")
        for name in ("total_call_pain", "total_put_pain", "total_pain"):
            _ensure_finite_decimal(getattr(self, name), InvalidOptionError, name)
            if getattr(self, name) < 0:
                raise InvalidOptionError(f"{name} cannot be negative")


@dataclass(frozen=True, slots=True)
class GammaAggregateItem:
    strike: Decimal
    total_gamma_exposure: Decimal
    call_gamma_exposure: Decimal
    put_gamma_exposure: Decimal
    net_gamma: Decimal
    contract_count: int
    absolute_gamma: Decimal = Decimal("0")
    open_interest: int = 0
    volume: int = 0

    def __post_init__(self) -> None:
        _ensure_positive_decimal(self.strike, InvalidStrikeError, "strike")
        for name in (
            "total_gamma_exposure",
            "call_gamma_exposure",
            "put_gamma_exposure",
            "net_gamma",
            "absolute_gamma",
        ):
            _ensure_finite_decimal(getattr(self, name), InvalidOptionError, name)
        if self.contract_count < 0:
            raise InvalidOptionError("contract_count cannot be negative")
        if self.open_interest < 0 or self.volume < 0:
            raise InvalidOptionError("open_interest and volume cannot be negative")


GammaAggregateStrike = GammaAggregateItem


@dataclass(frozen=True, slots=True)
class Wall:
    strike: Decimal
    gamma: Decimal
    open_interest: int
    volume: int

    def __post_init__(self) -> None:
        _ensure_positive_decimal(self.strike, InvalidStrikeError, "strike")
        _ensure_finite_decimal(self.gamma, InvalidOptionError, "gamma")
        if self.open_interest < 0 or self.volume < 0:
            raise InvalidOptionError("open_interest and volume cannot be negative")


@dataclass(frozen=True, slots=True)
class CallWall(Wall):
    pass


@dataclass(frozen=True, slots=True)
class PutWall(Wall):
    pass


@dataclass(frozen=True, slots=True)
class Walls:
    symbol: str
    as_of: datetime
    call_wall: CallWall | None = None
    put_wall: PutWall | None = None

    def __post_init__(self) -> None:
        if not self.symbol or not self.symbol.strip():
            raise InvalidOptionError("walls symbol is required")
        object.__setattr__(self, "symbol", self.symbol.upper())


@dataclass(frozen=True, slots=True)
class GammaAggregate:
    symbol: str
    as_of: datetime
    items: tuple[GammaAggregateItem, ...] = field(default_factory=tuple)
    total_market_gamma: Decimal = Decimal("0")
    positive_gamma: Decimal = Decimal("0")
    negative_gamma: Decimal = Decimal("0")
    total_gamma: Decimal = Decimal("0")
    # None means "no sign crossing found in the range of strikes looked
    # at" -- a real, distinct outcome from "the flip is at strike 0",
    # which a numeric-only field with a 0 default used to conflate
    # (confirmed live, 2026-09: SPX's net_gamma was positive at every
    # strike in range, which is exactly this case, not a bug).
    gamma_flip: Decimal | None = None
    call_wall: Decimal = Decimal("0")
    put_wall: Decimal = Decimal("0")
    max_pain: Decimal = Decimal("0")
    net_gamma: Decimal = Decimal("0")
    dealer_gamma_notional: Decimal = Decimal("0")
    vega_exposure: Decimal = Decimal(0)
    theta_exposure: Decimal = Decimal(0)
    charm_exposure: Decimal = Decimal(0)
    vanna_exposure: Decimal = Decimal(0)
    absolute_gamma_strike: Decimal = Decimal("0")
    peak_gamma_value: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if not self.symbol or not self.symbol.strip():
            raise InvalidOptionError("gamma aggregate symbol is required")
        object.__setattr__(self, "symbol", self.symbol.upper())
        for name in (
            "total_market_gamma",
            "positive_gamma",
            "negative_gamma",
            "total_gamma",
            "call_wall",
            "put_wall",
            "max_pain",
            "net_gamma",
            "dealer_gamma_notional",
            "vega_exposure",
            "theta_exposure",
            "charm_exposure",
            "vanna_exposure",
            "absolute_gamma_strike",
            "peak_gamma_value",
        ):
            _ensure_finite_decimal(getattr(self, name), InvalidOptionError, name)
        # gamma_flip is the one field here allowed to be None -- see its
        # own field comment above.
        if self.gamma_flip is not None:
            _ensure_finite_decimal(self.gamma_flip, InvalidOptionError, "gamma_flip")

    @property
    def strikes(self) -> tuple[GammaAggregateItem, ...]:
        return self.items

    @property
    def dealer_position(self) -> str:
        return dealer_position(self.net_gamma)


@dataclass(frozen=True, slots=True)
class DailyGammaReference:
    date: date
    symbol: str
    net_gamma: Decimal
    pc_oi_ratio: Decimal
    skew_25d: Decimal
    atm_iv: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", self.symbol.upper())
        for name in ("net_gamma", "pc_oi_ratio", "skew_25d", "atm_iv"):
            _ensure_finite_decimal(getattr(self, name), InvalidOptionError, name)
        if self.pc_oi_ratio < 0:
            raise InvalidOptionError("pc_oi_ratio cannot be negative")
        if self.atm_iv < 0:
            raise InvalidOptionError("atm_iv cannot be negative")


@dataclass(frozen=True, slots=True)
class DailyBar:
    """A single closed trading day's OHLC — raw material for True Range/ATR.

    Only ever represents a *closed* day; today's in-progress session is
    never stored here (see `calculate_atr_range`, which sources today's
    open from `market_snapshots` instead — same session-open reading
    `calculate_anchored_vwap` already uses).
    """

    symbol: str
    date: date
    open_price: Decimal
    high: Decimal
    low: Decimal
    close: Decimal

    def __post_init__(self) -> None:
        if not self.symbol or not self.symbol.strip():
            raise InvalidOptionError("daily bar symbol is required")
        object.__setattr__(self, "symbol", self.symbol.upper())
        for name in ("open_price", "high", "low", "close"):
            _ensure_finite_decimal(getattr(self, name), InvalidOptionError, name)
        if self.high < self.low:
            raise InvalidOptionError("daily bar high cannot be lower than low")
        if not self.low <= self.open_price <= self.high:
            raise InvalidOptionError("daily bar open must be within [low, high]")
        if not self.low <= self.close <= self.high:
            raise InvalidOptionError("daily bar close must be within [low, high]")


@dataclass(frozen=True, slots=True)
class MinuteBar:
    """A single closed 1-minute OHLCV bar from ThetaData's Indices Pro
    historical endpoint — the raw material for the `minute_bars` backfill
    (see `backend/scripts/backfill_minute_history.py`), not written by
    the live scheduler the way `MarketPrice`/`DailyBar` are.
    """

    symbol: str
    time: datetime
    open_price: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int

    def __post_init__(self) -> None:
        if not self.symbol or not self.symbol.strip():
            raise InvalidOptionError("minute bar symbol is required")
        object.__setattr__(self, "symbol", self.symbol.upper())
        for name in ("open_price", "high", "low", "close"):
            _ensure_finite_decimal(getattr(self, name), InvalidOptionError, name)
        if self.high < self.low:
            raise InvalidOptionError("minute bar high cannot be lower than low")
        if not self.low <= self.open_price <= self.high:
            raise InvalidOptionError("minute bar open must be within [low, high]")
        if not self.low <= self.close <= self.high:
            raise InvalidOptionError("minute bar close must be within [low, high]")
        if self.volume < 0:
            raise InvalidOptionError("minute bar volume cannot be negative")


@dataclass(frozen=True, slots=True)
class DerivedMetricValue:
    value: Decimal | None
    provisional: bool
    days_accumulated: int


@dataclass(frozen=True, slots=True)
class MarketBiasMetric:
    score: Decimal | None
    label: str | None
    provisional: bool
    days_accumulated: int


@dataclass(frozen=True, slots=True)
class VolatilityRegimeMetric:
    iv_rank: Decimal | None
    label: str | None
    provisional: bool
    days_accumulated: int


@dataclass(frozen=True, slots=True)
class DerivedMetrics:
    dealer_impact_score: DerivedMetricValue
    signal_alignment_score: DerivedMetricValue
    market_bias: MarketBiasMetric
    volatility_regime: VolatilityRegimeMetric


@dataclass(frozen=True, slots=True)
class GammaFlip:
    gamma_flip_price: Decimal | None = None
    lower_strike: Decimal | None = None
    upper_strike: Decimal | None = None
    lower_gamma: Decimal | None = None
    upper_gamma: Decimal | None = None
    interpolation_ratio: Decimal | None = None
    flip_found: bool = False

    def __post_init__(self) -> None:
        for name in (
            "gamma_flip_price",
            "lower_strike",
            "upper_strike",
            "lower_gamma",
            "upper_gamma",
            "interpolation_ratio",
        ):
            value = getattr(self, name)
            if value is not None:
                _ensure_finite_decimal(value, InvalidOptionError, name)
        if self.gamma_flip_price is not None:
            _ensure_positive_decimal(self.gamma_flip_price, InvalidStrikeError, "gamma_flip_price")
        if self.lower_strike is not None:
            _ensure_positive_decimal(self.lower_strike, InvalidStrikeError, "lower_strike")
        if self.upper_strike is not None:
            _ensure_positive_decimal(self.upper_strike, InvalidStrikeError, "upper_strike")


@dataclass(frozen=True, slots=True)
class GammaExposure:
    occ_symbol: str
    strike: Decimal
    contract_type: ContractType
    expiration: date
    gamma: Decimal
    open_interest: int
    dealer_gamma_exposure: Decimal
    sign: Decimal

    def __post_init__(self) -> None:
        if not self.occ_symbol:
            raise InvalidOptionError("occ_symbol is required")
        _ensure_positive_decimal(self.strike, InvalidStrikeError, "strike")
        if not isinstance(self.expiration, date):
            raise InvalidExpirationError("expiration must be a date")
        _ensure_finite_decimal(self.gamma, InvalidOptionError, "gamma")
        _ensure_finite_decimal(
            self.dealer_gamma_exposure,
            InvalidOptionError,
            "dealer_gamma_exposure",
        )
        _ensure_finite_decimal(self.sign, InvalidOptionError, "sign")
        if self.open_interest < 0:
            raise InvalidOptionError("open_interest cannot be negative")
        if self.sign not in (Decimal("-1"), Decimal("1")):
            raise InvalidOptionError("sign must be +1 or -1")


@dataclass(frozen=True, slots=True)
class MarketPrice:
    symbol: str
    as_of: datetime
    price: Decimal
    volume: int

    def __post_init__(self) -> None:
        if not self.symbol or not self.symbol.strip():
            raise InvalidOptionError("market price symbol is required")
        object.__setattr__(self, "symbol", self.symbol.upper())
        _ensure_finite_decimal(self.price, InvalidOptionError, "price")
        if self.price < 0 or self.volume < 0:
            raise InvalidOptionError("price and volume cannot be negative")


@dataclass(frozen=True, slots=True)
class FlowEvent:
    symbol: str
    occ_symbol: str
    as_of: datetime
    event_type: FlowEventType
    premium: Decimal
    size: int
    aggressor_side: AggressorSide


@dataclass(frozen=True, slots=True)
class QuoteEvent:
    """One prevailing bid/ask reading from a provider's live Quote Stream.

    The wire-level event a `stream_quotes()` implementation yields — see
    `LatestQuote` for the lighter per-contract state built from these
    (dropping `symbol`/`occ_symbol`, which the caller already has as the
    dict key), same split as `FlowEvent` (wire event, carries identity)
    versus `_ContractState` (per-contract state, does not).
    """

    symbol: str
    occ_symbol: str
    as_of: datetime
    bid: Decimal
    ask: Decimal


@dataclass(frozen=True, slots=True)
class LatestQuote:
    """The most recently known bid/ask for one contract.

    No time-indexed history — just whatever `QuoteEvent` arrived last for
    that `occ_symbol`, kept in a plain `dict[occ_symbol, LatestQuote]` by
    the consumer (`StreamWhaleAlertsUseCase`). "Vigente en ese instante"
    on a live stream reduces to "most recently received," matching the
    same timestamp precision the rest of this codebase already has for
    trades (`FlowEvent.as_of` is stamped at local receipt time too, not a
    reconciled on-exchange timestamp) — building anything more precise
    than this would outrun what the project's own trade timestamps
    already support.
    """

    bid: Decimal
    ask: Decimal
    as_of: datetime


@dataclass(frozen=True, slots=True)
class UnderlyingTradeEvent:
    """One raw trade tick from a provider's live Stock Trade Stream — the
    underlying's own price (e.g. SPY the equity), not an option contract's
    (see `FlowEvent`/`QuoteEvent` for those, both of which require an
    `occ_symbol` a bare stock tick doesn't have). Feeds
    `StreamUnderlyingPriceUseCase`, which persists it as `MarketPrice` —
    the same entity the REST scheduler already writes, additively, never
    replacing it.
    """

    symbol: str
    as_of: datetime
    price: Decimal
    size: int


@dataclass(frozen=True, slots=True)
class OptionSnapshot:
    contract: OptionContract
    greeks: OptionGreeks
    as_of: datetime


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    symbol: str
    as_of: datetime
    price: Decimal
    volume: int
    pc_oi_ratio: Decimal = Decimal("0")
    skew_25d: Decimal = Decimal("0")
    atm_iv: Decimal = Decimal("0")
    gamma: GammaAggregate | None = None
    expected_move: ExpectedMove | None = None
    anchored_vwap: AnchoredVwap | None = None
    atr_range: AtrRange | None = None
    closing_dynamics: ClosingDynamics | None = None
    recent_flow: tuple[FlowEvent, ...] = field(default_factory=tuple)
    state: MarketState = MarketState.UNKNOWN

    def __post_init__(self) -> None:
        if not self.symbol or not self.symbol.strip():
            raise InvalidOptionError("market snapshot symbol is required")
        object.__setattr__(self, "symbol", self.symbol.upper())
        _ensure_finite_decimal(self.price, InvalidOptionError, "price")
        _ensure_finite_decimal(self.pc_oi_ratio, InvalidOptionError, "pc_oi_ratio")
        _ensure_finite_decimal(self.skew_25d, InvalidOptionError, "skew_25d")
        _ensure_finite_decimal(self.atm_iv, InvalidOptionError, "atm_iv")
        if self.price < 0 or self.volume < 0:
            raise InvalidOptionError("price and volume cannot be negative")
        if self.pc_oi_ratio < 0:
            raise InvalidOptionError("pc_oi_ratio cannot be negative")
        if self.atm_iv < 0:
            raise InvalidOptionError("atm_iv cannot be negative")

    @property
    def dealer_mode(self) -> str:
        gamma = self._required_gamma()
        if self.dealer_mode_confirmed:
            return gamma.dealer_position
        # Only reached when _price_dealer_mode() disagreed with
        # dealer_position, which (see dealer_mode_confirmed) is only
        # possible when it returned a real value, not None.
        price_mode = self._price_dealer_mode()
        assert price_mode is not None
        return price_mode

    @property
    def dealer_mode_source(self) -> str:
        return "agree" if self.dealer_mode_confirmed else "price_vs_flip"

    @property
    def dealer_mode_confirmed(self) -> bool:
        price_mode = self._price_dealer_mode()
        if price_mode is None:
            # No gamma_flip to compare price against (no sign crossing
            # found in range -- see GammaAggregate.gamma_flip's own
            # comment). There's no independent signal to disagree with,
            # so this reports "confirmed" (dealer_position alone) rather
            # than inventing a third dealer_mode_source value the API
            # schema doesn't have -- flagged for review, not a fully
            # settled design choice.
            return True
        return self._required_gamma().dealer_position == price_mode

    @property
    def gamma_as_of(self) -> datetime:
        return self._required_gamma().as_of

    def _price_dealer_mode(self) -> str | None:
        gamma = self._required_gamma()
        if gamma.gamma_flip is None:
            return None
        # Project convention: a price exactly at gamma flip counts as long gamma.
        return "long_gamma" if self.price >= gamma.gamma_flip else "short_gamma"

    def _required_gamma(self) -> GammaAggregate:
        if self.gamma is None:
            raise InvalidOptionError("market snapshot requires a gamma aggregate")
        return self.gamma


@dataclass(frozen=True, slots=True)
class ExpectedMove:
    implied_1sd_dollars: Decimal
    implied_1sd_pct: Decimal
    remaining_1sd_dollars: Decimal
    remaining_1sd_pct: Decimal
    upper_bound: Decimal
    lower_bound: Decimal
    atm_iv: Decimal

    def __post_init__(self) -> None:
        for name in (
            "implied_1sd_dollars",
            "implied_1sd_pct",
            "remaining_1sd_dollars",
            "remaining_1sd_pct",
            "upper_bound",
            "lower_bound",
            "atm_iv",
        ):
            _ensure_finite_decimal(getattr(self, name), InvalidOptionError, name)


@dataclass(frozen=True, slots=True)
class AnchoredVwap:
    """VWAP anchored to the current session's 9:30 ET open.

    A read-only projection over already-persisted `market_snapshots`
    (see `calculate_anchored_vwap`) — never persisted itself.
    """

    value: Decimal | None
    provisional: bool
    anchor_time: datetime
    sample_count: int

    def __post_init__(self) -> None:
        if self.value is not None:
            _ensure_finite_decimal(self.value, InvalidOptionError, "value")
        if self.sample_count < 0:
            raise InvalidOptionError("anchored vwap sample_count cannot be negative")


@dataclass(frozen=True, slots=True)
class AtrRange:
    """ATR-anchored price band for the current session's open.

    Two independent provisional signals (see `docs/dashboard-spec.md`):
    `atr_provisional` (fewer than 15 days of closed `daily_bars` history —
    True Range needs a prior close, so 15 closed days yield 14 True Range
    values) and `bands_provisional` (no `market_snapshots` reading yet for
    today's session open — the ATR itself may already be available while
    the bands, which need today's open, are not). Never persisted — same
    read-only-projection pattern as `AnchoredVwap`.
    """

    atr: Decimal | None
    atr_provisional: bool
    daily_bars_count: int
    today_open: Decimal | None
    bands_provisional: bool
    outer_upper_band: Decimal | None
    outer_lower_band: Decimal | None
    inner_upper_band: Decimal | None
    inner_lower_band: Decimal | None

    def __post_init__(self) -> None:
        for name in (
            "atr",
            "today_open",
            "outer_upper_band",
            "outer_lower_band",
            "inner_upper_band",
            "inner_lower_band",
        ):
            value = getattr(self, name)
            if value is not None:
                _ensure_finite_decimal(value, InvalidOptionError, name)
        if self.daily_bars_count < 0:
            raise InvalidOptionError("atr range daily_bars_count cannot be negative")


@dataclass(frozen=True, slots=True)
class ClosingDynamics:
    """Charm/Vanna/Pin Risk read-out for the closing window (dashboard-spec.md section 9).

    `pin_score`, `magnet_strike`, `charm_regime` and `vanna_interpretation`
    are computed on every request — same "the data always exists" pattern
    as `AtrRange`/`AnchoredVwap`, never persisted. `active` is a separate,
    purely time-based signal (`time_to_close_pct` under the initial
    calibration threshold — see `calculate_closing_dynamics.py`) meant for
    a future frontend to decide visual prominence, not whether the values
    exist.
    """

    time_to_close_pct: Decimal
    active: bool
    pin_score: Decimal
    magnet_strike: Decimal | None
    charm_regime: str | None
    vanna_interpretation: str | None
    max_pain: Decimal

    def __post_init__(self) -> None:
        _ensure_finite_decimal(self.time_to_close_pct, InvalidOptionError, "time_to_close_pct")
        _ensure_finite_decimal(self.pin_score, InvalidOptionError, "pin_score")
        _ensure_finite_decimal(self.max_pain, InvalidOptionError, "max_pain")
        if self.magnet_strike is not None:
            _ensure_finite_decimal(self.magnet_strike, InvalidOptionError, "magnet_strike")
        if not (Decimal(0) <= self.pin_score <= Decimal(100)):
            raise InvalidOptionError("pin_score must be between 0 and 100")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def dealer_position(net_gamma: Decimal) -> str:
    return "long_gamma" if net_gamma >= 0 else "short_gamma"


def _ensure_finite_decimal(value: Decimal, error_type: type[DomainError], name: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise error_type(f"{name} must be a finite Decimal")


def _ensure_positive_decimal(value: Decimal, error_type: type[DomainError], name: str) -> None:
    _ensure_finite_decimal(value, error_type, name)
    if value <= 0:
        raise error_type(f"{name} must be greater than zero")
