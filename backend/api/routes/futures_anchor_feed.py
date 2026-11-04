"""POST /api/v1/feeds/futures-anchor: the 09:30 ES / NQ 1-minute bar from Lester's NinjaTrader (indicator 'Convexa Ancla', 2026-11-04).

Not behind the browser session (the indicator has none): a shared key in the X-Convexa-Key header, compared with the key file named in
settings (ninjatrader_anchor_key_file). The server recomputes the anchor from high / low / close and saves it only through
futures_anchor.auto_save_anchor: never over an anchor the day already has, never when the offset check fails. `dry_run` answers what it
would do without writing."""

from __future__ import annotations

import hmac
import logging
from datetime import datetime, time
from decimal import Decimal
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from backend.adapters.storage import futures_anchor_queries
from backend.core.container import Container
from backend.core.settings import get_settings
from backend.domain.use_cases.futures_anchor import (
    FUTURE_SPECS,
    anchor_from_bar,
    auto_save_anchor,
    normalize_future,
)
from backend.domain.use_cases.market_hours import EASTERN_TIME

logger = logging.getLogger(__name__)
router = APIRouter(tags=["feeds"])


class FuturesAnchorBar(BaseModel):
    symbol: str = Field(examples=["MES 12-26"])
    bar_start: datetime = Field(description="Start of the 1-minute bar; must be today's 09:30 ET.", examples=["2026-11-05T09:30:00-05:00"])
    high: Decimal = Field(gt=0)
    low: Decimal = Field(gt=0)
    close: Decimal = Field(gt=0)
    dry_run: bool = False


class FuturesAnchorResult(BaseModel):
    status: str
    future: str
    anchor: Decimal
    offset: float | None
    usual_offset: float | None
    warning: str | None
    existing: Decimal | None
    existing_source: str | None


def _key_matches(given: str | None) -> bool:
    path = Path(get_settings().ninjatrader_anchor_key_file)
    if not path.exists():
        raise HTTPException(status_code=503, detail="futures anchor feed is not configured")
    expected = path.read_text(encoding="ascii").strip()
    return bool(given) and bool(expected) and hmac.compare_digest(given.strip(), expected)


def _now_et() -> datetime:
    return datetime.now(EASTERN_TIME)


@router.post("/feeds/futures-anchor", response_model=FuturesAnchorResult)
def post_futures_anchor(body: FuturesAnchorBar, request: Request, x_convexa_key: str | None = Header(default=None)) -> FuturesAnchorResult:
    if not _key_matches(x_convexa_key):
        raise HTTPException(status_code=401, detail="bad key")
    future = normalize_future(body.symbol)
    if future is None:
        raise HTTPException(status_code=422, detail=f"{body.symbol!r} is not ES / MES / NQ / MNQ")
    start = body.bar_start.astimezone(EASTERN_TIME) if body.bar_start.tzinfo else body.bar_start.replace(tzinfo=EASTERN_TIME)
    today = _now_et().date()
    if start.date() != today or start.time() != time(9, 30):
        raise HTTPException(status_code=422, detail=f"only today's 09:30 ET bar is accepted (got {start:%Y-%m-%d %H:%M} ET)")
    container: Container = request.app.state.container
    storage = container.storage
    day_open = datetime.combine(today, time(9, 30), EASTERN_TIME)
    index_symbol = FUTURE_SPECS[future].index_symbol
    result = auto_save_anchor(
        storage,
        future,
        today,
        anchor_from_bar(body.high, body.low, body.close),
        "ninjatrader",
        futures_anchor_queries.first_minute_median(storage, index_symbol, day_open),
        futures_anchor_queries.recent_offsets(storage, future, index_symbol, day_open),
        dry_run=body.dry_run,
    )
    logger.info(
        "futures anchor from NinjaTrader: %s %s -> %s (offset %s, existing %s %s)%s",
        future, result.anchor, result.status, result.check.offset, result.existing, result.existing_source, " [dry run]" if body.dry_run else "",
    )
    return FuturesAnchorResult(
        status=result.status,
        future=future,
        anchor=result.anchor,
        offset=result.check.offset,
        usual_offset=result.check.usual,
        warning=result.check.warning,
        existing=result.existing,
        existing_source=result.existing_source,
    )
