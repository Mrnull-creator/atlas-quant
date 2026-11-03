
import logging
import sys

from atlas_quant.platform.config import get_settings


_CONFIGURED = False


def configure_logging(
    level: int = logging.INFO,
) -> None:
    """Configure consistent application-wide logging."""

    global _CONFIGURED

    if _CONFIGURED:
        return

    settings = get_settings()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt=(
                "%(asctime)s | %(levelname)s | "
                f"{settings.service_name} | "
                f"{settings.environment} | "
                "%(name)s | %(message)s"
            ),
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.addHandler(handler)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a named application logger."""
    return logging.getLogger(name)