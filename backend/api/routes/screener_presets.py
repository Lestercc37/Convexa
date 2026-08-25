from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from backend.api.schemas import (
    ScreenerPresetResponse,
    ScreenerPresetSettingsListResponse,
    ScreenerPresetSettingsResponse,
    ScreenerPresetSettingsUpdateRequest,
)
from backend.core.container import Container
from backend.domain.entities import (
    SCHEMA_VERSION,
    ExposureLeadersSettings,
    NegativeGammaBoardSettings,
    ScreenerPresetSettings,
)
from backend.domain.use_cases import ScreenerPreset, get_screener_preset

router = APIRouter(tags=["screener-presets"])

# Only these 3 presets have real, editable parameters (task decision) —
# Unusual Options Activity's config already lives in whale_thresholds
# (PR #69), and Max Pain & Key Levels has no scalar worth thresholding.
CONFIGURABLE_PRESETS = (
    ScreenerPreset.NEGATIVE_GAMMA_BOARD,
    ScreenerPreset.VANNA_EXPOSURE_LEADERS,
    ScreenerPreset.CHARM_DECAY_PRESSURE,
)


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
            for alert in container.whale_alerts_engine.recent_alerts(underlying.symbol)
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


def _settings_response(
    preset: ScreenerPreset, settings: ScreenerPresetSettings
) -> dict[str, object]:
    if isinstance(settings, NegativeGammaBoardSettings):
        return {
            "preset": preset.value,
            "net_gamma_max": settings.net_gamma_max,
            "min_magnitude": None,
            "limit": None,
        }
    return {
        "preset": preset.value,
        "net_gamma_max": None,
        "min_magnitude": settings.min_magnitude,
        "limit": settings.limit,
    }


def _default_settings(preset: ScreenerPreset) -> ScreenerPresetSettings:
    if preset is ScreenerPreset.NEGATIVE_GAMMA_BOARD:
        return NegativeGammaBoardSettings()
    return ExposureLeadersSettings()


@router.get(
    "/screener-preset-settings",
    response_model=ScreenerPresetSettingsListResponse,
)
def screener_preset_settings_list(request: Request) -> ScreenerPresetSettingsListResponse:
    container: Container = request.app.state.container
    settings_list = []
    for preset in CONFIGURABLE_PRESETS:
        settings = container.storage.get_screener_preset_settings(preset)
        if settings is None:
            settings = _default_settings(preset)
        settings_list.append(_settings_response(preset, settings))
    return ScreenerPresetSettingsListResponse.model_validate(
        {"schema_version": SCHEMA_VERSION, "settings": settings_list}
    )


@router.patch(
    "/screener-preset-settings/{preset_name}",
    response_model=ScreenerPresetSettingsResponse,
)
def update_screener_preset_settings(
    preset_name: str,
    body: ScreenerPresetSettingsUpdateRequest,
    request: Request,
) -> ScreenerPresetSettingsResponse:
    try:
        preset = ScreenerPreset.parse(preset_name)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown screener preset: {preset_name}",
        ) from error

    if preset not in CONFIGURABLE_PRESETS:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Preset '{preset.value}' has no editable settings",
        )

    provided = body.model_fields_set
    settings: ScreenerPresetSettings
    if preset is ScreenerPreset.NEGATIVE_GAMMA_BOARD:
        if (
            "net_gamma_max" not in provided
            or body.net_gamma_max is None
            or "min_magnitude" in provided
            or "limit" in provided
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    "negative-gamma-board requires exactly net_gamma_max "
                    "(min_magnitude and limit must be absent)"
                ),
            )
        settings = NegativeGammaBoardSettings(net_gamma_max=body.net_gamma_max)
    else:
        if (
            "min_magnitude" not in provided
            or "limit" not in provided
            or "net_gamma_max" in provided
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    f"{preset.value} requires both min_magnitude and limit "
                    "(each may be null; net_gamma_max must be absent)"
                ),
            )
        settings = ExposureLeadersSettings(min_magnitude=body.min_magnitude, limit=body.limit)

    container: Container = request.app.state.container
    container.storage.save_screener_preset_settings(preset, settings)
    return ScreenerPresetSettingsResponse.model_validate(_settings_response(preset, settings))
