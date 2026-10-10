from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, Request

from backend.api.deps import require_admin
from backend.api.schemas import FuturePriceAnchorRequest, FuturePriceAnchorResponse
from backend.core.container import Container
from backend.domain.entities import SCHEMA_VERSION
from backend.domain.ports import IStorage
from backend.domain.use_cases import PRICE_PROXY_SYMBOL_BY_FUTURE
from backend.domain.use_cases.errors import NotFoundError, OpeningPriceNotOpenYetError
from backend.domain.use_cases.futures_proxy import OpeningPriceWindow, opening_price_window

router = APIRouter(tags=["futures"])


def _now() -> datetime:
    return datetime.now(UTC)


def _opening_price_window(storage: IStorage, proxy_symbol: str) -> OpeningPriceWindow:
    """The session a typed opening price belongs to, and whether it may be saved right now.

    The session is anchored to the PROXY's (SPX/NDX) own latest stored price, same as
    future_level_offset() (read_models.py) itself, so the number is visible to the reads that
    resolve the session the same way (confirmed live, 2026-09-27: wall-clock "now" computed Sunday
    while SPX's last real price was still Friday's, and an anchor entered against Sunday never
    matched the Friday session the chart was reading).

    That is also why a number typed BEFORE today's first index price (9:30:02 ET) used to land on
    the previous session: opening_price_window() refuses it instead (see its docstring)."""
    latest_proxy_price = storage.get_latest_price(proxy_symbol)
    return opening_price_window(_now(), latest_proxy_price.as_of if latest_proxy_price is not None else None)


def _proxy_symbol_or_404(symbol: str) -> str:
    proxy_symbol = PRICE_PROXY_SYMBOL_BY_FUTURE.get(symbol.upper())
    if proxy_symbol is None:
        raise NotFoundError(f"{symbol.upper()} has no price-proxy future configured")
    return proxy_symbol


# GET is readable by every logged-in teammate (require_session, applied
# centrally in routes/__init__.py) -- only the PUT below, which actually
# changes what the chart shows, needs require_admin.
@router.get("/futures/{symbol}/opening-price", response_model=FuturePriceAnchorResponse)
def get_future_opening_price(symbol: str, request: Request) -> FuturePriceAnchorResponse:
    normalized = symbol.upper()
    proxy_symbol = _proxy_symbol_or_404(normalized)
    container: Container = request.app.state.container
    window = _opening_price_window(container.storage, proxy_symbol)
    anchor = container.storage.get_future_price_anchor(normalized, window.session_date)
    saved_at = (
        container.storage.get_future_price_anchor_saved_at(normalized, window.session_date)
        if anchor is not None
        else None
    )
    return _response(normalized, proxy_symbol, window, anchor, saved_at)


@router.put(
    "/futures/{symbol}/opening-price",
    response_model=FuturePriceAnchorResponse,
    dependencies=[Depends(require_admin)],
)
def set_future_opening_price(
    symbol: str, body: FuturePriceAnchorRequest, request: Request
) -> FuturePriceAnchorResponse:
    normalized = symbol.upper()
    proxy_symbol = _proxy_symbol_or_404(normalized)
    container: Container = request.app.state.container
    window = _opening_price_window(container.storage, proxy_symbol)
    if not window.accepting:
        raise OpeningPriceNotOpenYetError(
            f"Wait for {proxy_symbol}'s first price of today (9:30:02 ET). The number is the {normalized} "
            f"price at 9:30:00 (the open of the 1-minute candle), not the current price."
        )
    container.storage.set_future_price_anchor(normalized, window.session_date, body.opening_price)
    saved_at = container.storage.get_future_price_anchor_saved_at(normalized, window.session_date)
    return _response(normalized, proxy_symbol, window, body.opening_price, saved_at)


def _response(
    symbol: str,
    proxy_symbol: str,
    window: OpeningPriceWindow,
    opening_price: Decimal | None,
    saved_at: datetime | None,
) -> FuturePriceAnchorResponse:
    return FuturePriceAnchorResponse.model_validate(
        {
            "schema_version": SCHEMA_VERSION,
            "symbol": symbol,
            "proxy_symbol": proxy_symbol,
            "session_date": window.session_date,
            "opening_price": opening_price,
            "saved_at": saved_at,
            "accepting": window.accepting,
            "waiting_reason": window.waiting_reason,
        }
    )
