from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Body, HTTPException, Request

from backend.api.schemas import (
    GammaAggregateResponse,
    GammaExposureResponse,
    GammaFlipRequest,
    GammaFlipResponse,
    MaxPainResponse,
    WallsResponse,
    GreeksResponse,
    OptionChainRequest,
    OptionChainResponse,
)
from backend.api.serializers import (
    chain_response,
    gamma_aggregate_response,
    gamma_exposure_response,
    gamma_flip_response,
    max_pain_response,
    walls_response,
    greeks_chain_response,
)
from backend.core.container import Container
from backend.domain.entities import DomainError, OptionChain

OPTION_CHAIN_REQUEST_EXAMPLE = {
    "symbol": "SPY",
    "as_of": "2026-01-15T14:30:00Z",
    "spot_price": 552.25,
    "contracts": [
        {
            "occ_symbol": "SPY260220C00540000",
            "underlying": "SPY",
            "strike": 540,
            "expiration": "2026-02-20",
            "type": "call",
            "bid": 1.2,
            "ask": 1.25,
            "last": 1.22,
            "iv": 0.18,
            "delta": 0,
            "gamma": 0.03,
            "theta": -0.015,
            "vega": 0.12,
            "charm": -0.001,
            "vanna": 0.02,
            "open_interest": 8000,
            "volume": 3400,
        }
    ],
}

OptionChainBody = Annotated[
    OptionChainRequest,
    Body(
        openapi_examples={
            "option_chain": {
                "summary": "Option chain request",
                "value": OPTION_CHAIN_REQUEST_EXAMPLE,
            }
        },
    ),
]

GammaFlipBody = Annotated[
    GammaFlipRequest,
    Body(
        openapi_examples={
            "gamma_aggregate": {
                "summary": "Gamma Aggregate request",
                "value": {
                    "symbol": "SPY",
                    "as_of": "2026-01-15T14:30:00Z",
                    "items": [
                        {
                            "strike": 540,
                            "total_gamma_exposure": 390,
                            "call_gamma_exposure": 240,
                            "put_gamma_exposure": 150,
                            "net_gamma": 90,
                            "contract_count": 2,
                            "absolute_gamma": 90,
                        },
                        {
                            "strike": 545,
                            "total_gamma_exposure": 210,
                            "call_gamma_exposure": 200,
                            "put_gamma_exposure": 10,
                            "net_gamma": -10,
                            "contract_count": 2,
                            "absolute_gamma": 10,
                        },
                    ],
                },
            }
        },
    ),
]

router = APIRouter(tags=["options"])


@router.get(
    "/options/{symbol}",
    response_model=OptionChainResponse,
    summary="Load option chain",
)
def load_option_chain(
    symbol: str,
    request: Request,
    expiration: date | None = None,
) -> OptionChainResponse:
    container: Container = request.app.state.container
    chain = container.load_option_chain_use_case.execute(symbol, expiration)
    return OptionChainResponse.model_validate(chain_response(chain))


@router.post(
    "/options/greeks",
    response_model=GreeksResponse,
    summary="Calculate deterministic Greeks for an option chain",
)
def calculate_greeks(payload: OptionChainBody, request: Request) -> GreeksResponse:
    print(
        "calculate_greeks runtime evidence:",
        {
            "__file__": __file__,
            "co_firstlineno": calculate_greeks.__code__.co_firstlineno,
            "payload_type": type(payload),
            "option_chain_from_payload_exists": "option_chain_from_payload" in globals(),
            "calculate_greeks_calls_option_chain_from_payload": "option_chain_from_payload"
            in calculate_greeks.__code__.co_names,
        },
        flush=True,
    )
    container: Container = request.app.state.container
    chain = _chain_from_request(payload)
    enriched_chain = container.calculate_greeks_use_case.execute(chain)
    return GreeksResponse.model_validate(greeks_chain_response(enriched_chain))


@router.post(
    "/options/gamma-aggregate",
    response_model=GammaAggregateResponse,
    summary="Calculate Gamma Aggregate by strike for an option chain",
)
def calculate_gamma_aggregate(payload: OptionChainBody, request: Request) -> GammaAggregateResponse:
    container: Container = request.app.state.container
    chain = _chain_from_request(payload)
    gamma_aggregate = container.calculate_gamma_aggregate_use_case.execute(chain)
    return GammaAggregateResponse.model_validate(gamma_aggregate_response(gamma_aggregate))


@router.post(
    "/options/gamma-exposure",
    response_model=GammaExposureResponse,
    summary="Calculate deterministic Gamma Exposure for an option chain",
)
def calculate_gamma_exposure(payload: OptionChainBody, request: Request) -> GammaExposureResponse:
    container: Container = request.app.state.container
    chain = _chain_from_request(payload)
    gamma_exposures = container.calculate_gamma_exposure_use_case.execute(chain)
    return GammaExposureResponse.model_validate(gamma_exposure_response(gamma_exposures))


@router.post(
    "/options/max-pain",
    response_model=MaxPainResponse,
    summary="Calculate institutional Max Pain for an option chain",
)
def calculate_max_pain(payload: OptionChainBody, request: Request) -> MaxPainResponse:
    container: Container = request.app.state.container
    chain = _chain_from_request(payload)
    max_pain = container.calculate_max_pain_use_case.execute(chain)
    return MaxPainResponse.model_validate(max_pain_response(max_pain))


@router.post(
    "/options/walls",
    response_model=WallsResponse,
    summary="Calculate institutional Call Wall and Put Wall from Gamma Aggregate",
)
def calculate_walls(payload: GammaFlipBody, request: Request) -> WallsResponse:
    container: Container = request.app.state.container
    aggregate = _gamma_aggregate_from_request(payload)
    walls = container.calculate_walls_use_case.execute(aggregate)
    return WallsResponse.model_validate(walls_response(walls))


@router.post(
    "/options/gamma-flip",
    response_model=GammaFlipResponse,
    summary="Calculate Gamma Flip from Gamma Aggregate",
)
def calculate_gamma_flip(payload: GammaFlipBody, request: Request) -> GammaFlipResponse:
    container: Container = request.app.state.container
    aggregate = _gamma_aggregate_from_request(payload)
    gamma_flip = container.calculate_gamma_flip_use_case.execute(aggregate)
    return GammaFlipResponse.model_validate(gamma_flip_response(gamma_flip))


def _chain_from_request(payload: OptionChainRequest) -> OptionChain:
    try:
        return payload.to_domain()
    except DomainError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _gamma_aggregate_from_request(payload: GammaFlipRequest):
    try:
        return payload.to_domain()
    except DomainError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


