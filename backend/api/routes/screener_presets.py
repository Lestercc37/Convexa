from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from backend.api.schemas import ScreenerPresetResponse
from backend.core.container import Container
from backend.domain.entities import SCHEMA_VERSION
from backend.domain.use_cases import ScreenerPreset, get_screener_preset

router = APIRouter(tags=["screener-presets"])


@router.get(
    "/screener-presets/{preset_name}",
    response_model=ScreenerPresetResponse,
)
def screener_preset(preset_name: str, request: Request) -> ScreenerPresetResponse:
    try:
        preset = ScreenerPreset.parse(preset_name)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown screener preset: {preset_name}",
        ) from error

    container: Container = request.app.state.container
    alerts = ()
    if preset is ScreenerPreset.UNUSUAL_OPTIONS_ACTIVITY:
        alerts = tuple(
            alert
            for underlying in container.storage.list_underlyings()
            for alert in container.eagle_contracts_engine.recent_alerts(underlying.symbol)
        )
    results = get_screener_preset(container.storage, preset, alerts)
    return ScreenerPresetResponse.model_validate(
        {
            "schema_version": SCHEMA_VERSION,
            "preset": preset,
            "results": [
                {
                    "symbol": item.symbol,
                    "as_of": item.as_of.isoformat(),
                    "contract": item.contract,
                    "alert_type": item.alert_type,
                    "amount": item.amount,
                    "net_gamma": item.net_gamma,
                    "gamma_flip": item.gamma_flip,
                    "call_wall": item.call_wall,
                    "put_wall": item.put_wall,
                    "max_pain": item.max_pain,
                    "vanna_exposure": item.vanna_exposure,
                    "charm_exposure": item.charm_exposure,
                }
                for item in results
            ],
        }
    )
