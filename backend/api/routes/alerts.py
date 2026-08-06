from __future__ import annotations

from fastapi import APIRouter, Query, Request

from backend.api.schemas import EagleAlertsResponse
from backend.core.container import Container
from backend.domain.entities import SCHEMA_VERSION

router = APIRouter(tags=["alerts"])


@router.get("/alerts/{symbol}", response_model=EagleAlertsResponse)
def get_alerts(
    symbol: str,
    request: Request,
    limit: int = Query(default=100, ge=1, le=1000),
) -> EagleAlertsResponse:
    container: Container = request.app.state.container
    normalized = symbol.upper()
    alerts = container.eagle_contracts_engine.recent_alerts(normalized, limit)
    return EagleAlertsResponse.model_validate(
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
                }
                for alert in alerts
            ],
        }
    )
