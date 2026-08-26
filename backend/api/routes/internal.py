from __future__ import annotations

from fastapi import APIRouter, Request

from backend.api.schemas import GammaResponse, TriggerCalculationResponse
from backend.api.serializers import gamma_response
from backend.core.container import Container
from backend.domain.entities import SCHEMA_VERSION

router = APIRouter(prefix="/internal", tags=["internal"])


@router.post(
    "/trigger-calculation/{symbol}",
    response_model=TriggerCalculationResponse,
    include_in_schema=False,
)
def trigger_calculation(
    symbol: str,
    request: Request,
) -> TriggerCalculationResponse:
    container: Container = request.app.state.container

    aggregate, derived_metrics = container.refresh_underlying_snapshot_use_case.execute(symbol)
    gamma_payload = GammaResponse.model_validate(gamma_response(aggregate, derived_metrics))
    return TriggerCalculationResponse(
        schema_version=SCHEMA_VERSION,
        symbol=aggregate.symbol,
        status="calculated",
        gamma=gamma_payload,
    )
