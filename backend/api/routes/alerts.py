from __future__ import annotations

from fastapi import APIRouter, Query, Request

from backend.api.schemas import WhaleAlertsResponse
from backend.core.container import Container
from backend.domain.entities import SCHEMA_VERSION

router = APIRouter(tags=["alerts"])


@router.get("/alerts/{symbol}", response_model=WhaleAlertsResponse)
async def get_alerts(
    symbol: str,
    request: Request,
    limit: int = Query(default=100, ge=1, le=1000),
) -> WhaleAlertsResponse:
    # async def, not def: same reasoning as /gamma and /market (see
    # container.async_market_storage's own docstring) -- a pure storage
    # read that would otherwise share Starlette's threadpool with the
    # scheduler's blocking work. Reads whale_alerts (Postgres), not
    # whale_alerts_engine.recent_alerts (in-memory) -- confirmed live,
    # 2026-09, that these agree; the in-memory deque's 1000-alert cap
    # (shared across every symbol combined) no longer applies here,
    # since Postgres has no equivalent limit.
    container: Container = request.app.state.container
    normalized = symbol.upper()
    alerts = await container.async_market_storage.get_recent_whale_alerts(normalized, limit)
    return WhaleAlertsResponse.model_validate(
        {
            "schema_version": SCHEMA_VERSION,
            "symbol": normalized,
            "alerts": [
                {
                    "symbol": alert.symbol,
                    "contract": alert.occ_symbol,
                    "type": alert.alert_type,
                    "amount": alert.amount,
                    "timestamp": alert.as_of.isoformat(),
                    "estimated_buy_volume": alert.estimated_buy_volume,
                    "estimated_sell_volume": alert.estimated_sell_volume,
                }
                for alert in alerts
            ],
        }
    )
