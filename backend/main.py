from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from backend.api.routes import api_router
from backend.api.serializers import error_response
from backend.core.container import build_container
from backend.core.logging import configure_logging
from backend.domain.use_cases.errors import QllError

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    container = build_container()
    configure_logging(container.settings)
    app.state.container = container
    logger.info("Starting %s", container.settings.app_name)
    try:
        yield
    finally:
        if container.storage_engine is not None:
            container.storage_engine.dispose()
        await container.database_engine.dispose()
        logger.info("Stopping %s", container.settings.app_name)


def create_app() -> FastAPI:
    container = build_container()
    app = FastAPI(
        title=container.settings.app_name,
        version=container.settings.version,
        description="Backend API for QLL Eagle Platform.",
        openapi_url=container.settings.openapi_url,
        docs_url=container.settings.docs_url,
        redoc_url=container.settings.redoc_url,
        lifespan=lifespan,
    )
    app.state.container = container

    @app.exception_handler(QllError)
    async def qll_error_handler(request: Request, error: QllError) -> JSONResponse:
        del request
        status_code = 404 if error.code == "NOT_FOUND" else 500
        return JSONResponse(status_code=status_code, content=error_response(error))

    app.include_router(api_router())
    return app


app = create_app()
