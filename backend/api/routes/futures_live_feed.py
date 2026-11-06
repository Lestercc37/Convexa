"""Live ES / NQ / RTY from Lester's NinjaTrader ('Convexa Feed' indicator, docs/ninjatrader/ConvexaFeed.cs, 2026-11-06).

POST /api/v1/feeds/futures-live: not behind the browser session (the indicator has none), the same shared key as the anchor feed
(X-Convexa-Key, futures_anchor_feed._key_matches). About once a second per chart: the last price, plus every closed 1-minute bar.
GET /api/v1/futures/{symbol}/live (session, see backend/api/routes/futures.py): the basis in use and, for the owner only, the real last price.
See backend/domain/use_cases/futures_live.py for what is stored and why."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from backend.api.routes.futures_anchor_feed import _key_matches
from backend.core.container import Container
from backend.domain.use_cases.futures_events import EVENT_TYPES, INDEX_OF, SIDES, FuturesEvent, translate
from backend.domain.use_cases.futures_live import (
    LIVE_FUTURES,
    QUOTE_CLOCK_SKEW,
    FuturesLiveBar,
    FuturesLiveQuote,
    is_rth,
    live_ratio,
    normalize_live_future,
)
from backend.domain.use_cases.futures_proxy import future_level_offset
from backend.domain.use_cases.index_stale_guard import STATUS_STALE
from backend.domain.use_cases.market_hours import EASTERN_TIME

logger = logging.getLogger(__name__)
router = APIRouter(tags=["feeds"])

MAX_BARS_PER_POST = 500
# A last price further than this from the previous one is a bad print or a wrong chart: not stored.
MAX_QUOTE_JUMP = Decimal("0.05")


class LiveQuoteIn(BaseModel):
    symbol: str = Field(examples=["ES"], description="NinjaTrader's master instrument name (ES / MES / NQ / MNQ / RTY / M2K)")
    contract: str = Field(examples=["ES 12-26"])
    last: Decimal = Field(gt=0)
    bid: Decimal | None = Field(default=None, gt=0)
    ask: Decimal | None = Field(default=None, gt=0)
    time: datetime = Field(description="NinjaTrader's time of the last price, with its offset")


class LiveBarIn(BaseModel):
    symbol: str
    contract: str
    start: datetime = Field(description="START of the 1-minute bar, with its offset")
    open: Decimal = Field(gt=0)
    high: Decimal = Field(gt=0)
    low: Decimal = Field(gt=0)
    close: Decimal = Field(gt=0)
    volume: int = Field(ge=0)


class LiveFeedIn(BaseModel):
    quotes: list[LiveQuoteIn] = Field(default_factory=list)
    bars: list[LiveBarIn] = Field(default_factory=list)


class LiveFeedResult(BaseModel):
    quotes_saved: int
    bars_saved: int
    skipped: list[str]


def _index_replaced(storage, index_symbol: str, now: datetime) -> bool:
    """True while index_stale_guard writes `index_symbol` from its ETF: a basis against it would be a basis against a stale index."""
    for status in storage.get_index_price_fallbacks():
        if status.symbol == index_symbol and status.active and now - status.updated_at <= STATUS_STALE:
            return True
    return False


def _index_price_in(storage, index_symbol: str, minute: datetime) -> Decimal | None:
    readings = storage.get_price_history(index_symbol, minute, minute + timedelta(seconds=59, microseconds=999_999))
    return readings[-1].price if readings else None


@router.post("/feeds/futures-live", response_model=LiveFeedResult)
def post_futures_live(body: LiveFeedIn, request: Request, x_convexa_key: str | None = Header(default=None)) -> LiveFeedResult:
    if not _key_matches(x_convexa_key):
        raise HTTPException(status_code=401, detail="bad key")
    if len(body.bars) > MAX_BARS_PER_POST:
        raise HTTPException(status_code=422, detail=f"at most {MAX_BARS_PER_POST} bars per post")
    container: Container = request.app.state.container
    storage = container.storage
    now = datetime.now(UTC)
    skipped: list[str] = []
    quotes_saved = 0
    for quote in body.quotes:
        future = normalize_live_future(quote.symbol)
        if future is None or quote.time.tzinfo is None:
            skipped.append(f"quote {quote.symbol}: not ES / NQ / RTY or no time offset")
            continue
        if abs(now - quote.time) > QUOTE_CLOCK_SKEW:
            skipped.append(f"quote {future}: NinjaTrader time {quote.time.isoformat()} is {abs(now - quote.time).total_seconds():.0f} s from the server's")
            continue
        previous = storage.get_futures_live_quote(future)
        if previous is not None and previous.contract == quote.contract and abs(quote.last / previous.last - 1) > MAX_QUOTE_JUMP:
            skipped.append(f"quote {future}: {quote.last} jumps more than 5 % from {previous.last}")
            continue
        storage.save_futures_live_quote(FuturesLiveQuote(future, quote.contract, quote.last, quote.bid, quote.ask, quote.time, now))
        quotes_saved += 1

    bars: list[FuturesLiveBar] = []
    replaced: dict[str, bool] = {}
    for bar in body.bars:
        future = normalize_live_future(bar.symbol)
        if future is None or bar.start.tzinfo is None or not (bar.low <= bar.open <= bar.high and bar.low <= bar.close <= bar.high):
            skipped.append(f"bar {bar.symbol} {bar.start.isoformat()}: not ES / NQ / RTY, no offset, or OHLC out of order")
            continue
        minute = bar.start.astimezone(UTC).replace(second=0, microsecond=0)
        rth = is_rth(minute.astimezone(EASTERN_TIME))
        index_symbol = LIVE_FUTURES[future]
        if index_symbol not in replaced:
            replaced[index_symbol] = _index_replaced(storage, index_symbol, now)
        index_price = _index_price_in(storage, index_symbol, minute) if rth and not replaced[index_symbol] else None
        bars.append(
            FuturesLiveBar(future, bar.contract, minute, bar.open, bar.high, bar.low, bar.close, bar.volume, "rth" if rth else "eth", index_price, now)
        )
    storage.save_futures_live_bars(bars)
    if skipped:
        logger.info("futures live feed: %d quote(s), %d bar(s) saved; skipped: %s", quotes_saved, len(bars), "; ".join(skipped[:5]))
    return LiveFeedResult(quotes_saved=quotes_saved, bars_saved=len(bars), skipped=skipped)


class FuturesEventIn(BaseModel):
    symbol: str = Field(examples=["ES"])
    contract: str = Field(examples=["ES 12-26"])
    time: datetime = Field(description="when NinjaTrader detected it, with its offset")
    type: str = Field(examples=["absorption"], description="absorption | stacked_imbalance | big_trade")
    side: str = Field(examples=["buy"], description="buy = buyers win (selling absorbed at the bid, stacked buy imbalances, a big buy)")
    price: Decimal = Field(gt=0)
    price_low: Decimal | None = Field(default=None, gt=0)
    price_high: Decimal | None = Field(default=None, gt=0)
    volume: int = Field(ge=0)
    detail: str = Field(default="", max_length=200)


class FuturesEventsIn(BaseModel):
    events: list[FuturesEventIn]


class FuturesEventsResult(BaseModel):
    saved: int
    skipped: list[str]


def _index_etf_ratio(storage, index: str) -> float | None:
    """index / its ETF from the latest stored prices (SPX / SPY ~10.03, NDX / QQQ ~41.1): the ETF chart's price of an index level."""
    from backend.domain.use_cases.futures_events import ETF_OF_INDEX

    index_price = storage.get_latest_price(index)
    etf_price = storage.get_latest_price(ETF_OF_INDEX[index])
    if index_price is None or etf_price is None or etf_price.price <= 0:
        return None
    return float(index_price.price / etf_price.price)


@router.post("/feeds/futures-events", response_model=FuturesEventsResult)
def post_futures_events(body: FuturesEventsIn, request: Request, x_convexa_key: str | None = Header(default=None)) -> FuturesEventsResult:
    """Absorption / stacked imbalance / big trade events from 'Convexa Detector' (use_cases/futures_events.py), translated and stored."""
    if not _key_matches(x_convexa_key):
        raise HTTPException(status_code=401, detail="bad key")
    if len(body.events) > 200:
        raise HTTPException(status_code=422, detail="at most 200 events per post")
    container: Container = request.app.state.container
    storage = container.storage
    now = datetime.now(UTC)
    saved: list[FuturesEvent] = []
    skipped: list[str] = []
    for event in body.events:
        future = normalize_live_future(event.symbol)
        if future is None or event.type not in EVENT_TYPES or event.side not in SIDES or event.time.tzinfo is None:
            skipped.append(f"{event.symbol} {event.type} {event.side}: unknown future / type / side, or no time offset")
            continue
        basis = ratio = rty = None
        if future in INDEX_OF:
            basis = future_level_offset(storage, future, INDEX_OF[future])
            ratio = _index_etf_ratio(storage, INDEX_OF[future])
        else:
            found = live_ratio(storage.get_futures_live_bars("RTY", now - timedelta(minutes=20)), now)
            rty = found[0] if found else None
        saved.append(
            FuturesEvent(
                future, event.contract, event.time.astimezone(UTC), event.type, event.side, event.price,
                event.price_low or event.price, event.price_high or event.price, event.volume, event.detail,
                translate(future, event.price, basis, ratio, rty), now,
            )
        )
    storage.save_futures_events(saved)
    if skipped:
        logger.info("futures events: %d saved; skipped: %s", len(saved), "; ".join(skipped[:5]))
    return FuturesEventsResult(saved=len(saved), skipped=skipped)
