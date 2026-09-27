from __future__ import annotations

from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, Request

from backend.api.deps import require_admin
from backend.api.schemas import FuturePriceAnchorRequest, FuturePriceAnchorResponse
from backend.core.container import Container
from backend.domain.entities import SCHEMA_VERSION
from backend.domain.ports import IStorage
from backend.domain.use_cases import PRICE_PROXY_SYMBOL_BY_FUTURE, calculate_session_open
from backend.domain.use_cases.errors import NotFoundError

router = APIRouter(tags=["futures"])


def _current_session_date(storage: IStorage, proxy_symbol: str) -> date:
    """The session this anchor belongs to -- anchored to the PROXY's
    (SPX/NDX) own latest stored price, same as future_price_offset()
    (read_models.py) itself, not wall-clock now. Must match exactly, or
    an anchor saved under "today" (wall-clock) is invisible to a read
    that resolves the session from the proxy's actual last trading
    session -- confirmed live, 2026-09-27: over a weekend, wall-clock
    now() computed Sunday while SPX's own last real price was still
    Friday's, so an anchor entered against Sunday's date never matched
    the Friday session the chart was actually reading. Falls back to
    wall-clock only when the proxy has no price at all yet (never
    happens for SPX/NDX in practice, but keeps this total)."""
    latest_proxy_price = storage.get_latest_price(proxy_symbol)
    as_of = latest_proxy_price.as_of if latest_proxy_price is not None else datetime.now(UTC)
    return calculate_session_open(as_of).date()


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
    session_date = _current_session_date(container.storage, proxy_symbol)
    anchor = container.storage.get_future_price_anchor(normalized, session_date)
    return FuturePriceAnchorResponse.model_validate(
        {
            "schema_version": SCHEMA_VERSION,
            "symbol": normalized,
            "proxy_symbol": proxy_symbol,
            "session_date": session_date,
            "opening_price": anchor,
        }
    )


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
    session_date = _current_session_date(container.storage, proxy_symbol)
    container.storage.set_future_price_anchor(normalized, session_date, body.opening_price)
    return FuturePriceAnchorResponse.model_validate(
        {
            "schema_version": SCHEMA_VERSION,
            "symbol": normalized,
            "proxy_symbol": proxy_symbol,
            "session_date": session_date,
            "opening_price": body.opening_price,
        }
    )
