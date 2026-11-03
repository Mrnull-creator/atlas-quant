from fastapi import Request
from fastapi.responses import JSONResponse

from atlas_quant.platform.errors import AtlasQuantError
from atlas_quant.platform.logging import get_logger


logger = get_logger(__name__)


async def atlas_quant_exception_handler(
    request: Request,
    exc: AtlasQuantError,
) -> JSONResponse:
    """Convert Atlas Quant application errors into standard API responses."""

    status_code = getattr(exc, "http_status_code", 500)

    logger.warning(
        "Application error %s on %s %s",
        exc.error_code,
        request.method,
        request.url.path,
    )

    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": exc.error_code,
                "message": exc.message,
            }
        },
    )


async def unexpected_exception_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    """Return a safe response for unexpected application failures."""

    logger.error(
        "Unhandled exception on %s %s: %s",
        request.method,
        request.url.path,
        type(exc).__name__,
        exc_info=(type(exc), exc, exc.__traceback__),
    )

    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "INTERNAL_SERVER_ERROR",
                "message": "An unexpected internal error occurred.",
            }
        },
    )