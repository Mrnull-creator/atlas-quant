from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from atlas_quant.platform.config import get_settings
from atlas_quant.platform.errors import AtlasQuantError
from atlas_quant.platform.logging import configure_logging, get_logger
from atlas_quant.services.common.exception_handlers import (
    atlas_quant_exception_handler,
    unexpected_exception_handler,
)


def create_service_app(
    *,
    service_name: str | None = None,
    version: str | None = None,
) -> FastAPI:
    """Create a standardized FastAPI application for an Atlas Quant service."""

    settings = get_settings()

    resolved_name = service_name or settings.service_name
    resolved_version = version or settings.service_version

    logger = get_logger(f"atlas_quant.services.{resolved_name}")

    app = FastAPI(
        title=resolved_name,
        version=resolved_version,
    )

    app.state.service_name = resolved_name
    app.state.ready = False

    app.add_exception_handler(
        AtlasQuantError,
        atlas_quant_exception_handler,
    )

    app.add_exception_handler(
        Exception,
        unexpected_exception_handler,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Manage service startup and graceful shutdown."""

        configure_logging()
        logger.info("Service starting")

        app.state.ready = True

        try:
            yield
        finally:
            app.state.ready = False
            logger.info("Service shutting down")

    app.router.lifespan_context = lifespan

    @app.get("/health/live", tags=["Health"])
    async def liveness() -> dict[str, bool | str]:
        """Report whether the service process is alive."""

        return {
            "alive": True,
            "service": resolved_name,
        }

    @app.get("/health/ready", tags=["Health"])
    async def readiness() -> dict[str, bool | str]:
        """Report whether the service has completed startup."""

        return {
            "ready": app.state.ready,
            "service": resolved_name,
        }

    return app