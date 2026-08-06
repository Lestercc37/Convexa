from __future__ import annotations

from fastapi import APIRouter

from backend.api.routes.alerts import router as alerts_router
from backend.api.routes.health import router as health_router
from backend.api.routes.internal import router as internal_router
from backend.api.routes.market import router as market_router
from backend.api.routes.options import router as options_router
from backend.api.routes.screener_presets import router as screener_presets_router


def api_router() -> APIRouter:
    router = APIRouter()
    router.include_router(health_router)
    router.include_router(internal_router)
    router.include_router(market_router, prefix="/api/v1")
    router.include_router(options_router, prefix="/api/v1")
    router.include_router(alerts_router, prefix="/api/v1")
    router.include_router(screener_presets_router, prefix="/api/v1")
    return router
