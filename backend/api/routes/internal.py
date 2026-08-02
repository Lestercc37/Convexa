from __future__ import annotations

from fastapi import APIRouter, Request

from backend.api.schemas import GammaResponse, TriggerCalculationResponse
from backend.api.serializers import gamma_response
from backend.core.container import Container
from backend.domain.entities import MarketPrice, SCHEMA_VERSION

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

    chain = container.market_data_provider.get_option_chain(symbol)
    container.storage.save_chain_snapshot(chain)

    market = container.market_data_provider.get_underlying_snapshot(symbol)
    container.storage.save_market_price(
        MarketPrice(
            symbol=market.symbol,
            as_of=market.as_of,
            price=market.price,
            volume=market.volume,
        )
    )

    aggregate = container.calculate_gamma_exposure_orchestrator.execute(symbol)
    gamma_payload = GammaResponse.model_validate(gamma_response(aggregate))
    return TriggerCalculationResponse(
        schema_version=SCHEMA_VERSION,
        symbol=aggregate.symbol,
        status="calculated",
        gamma=gamma_payload,
    )
