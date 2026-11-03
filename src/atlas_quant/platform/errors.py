from typing import Any


class AtlasQuantError(Exception):
    """Base exception for Atlas Quant application errors."""

    error_code = "ATLAS_ERROR"
    http_status_code = 500

    def __init__(
        self,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        http_status_code: int | None = None,
    ) -> None:
        self.message = message
        self.details = details or {}

        if http_status_code is not None:
            self.http_status_code = http_status_code

        super().__init__(message)

    def to_dict(self) -> dict[str, Any]:
        """Return a standardized, serializable error representation."""

        return {
            "error_code": self.error_code,
            "message": self.message,
            "details": self.details,
        }


class ConfigurationError(AtlasQuantError):
    """Raised when application configuration is invalid."""

    error_code = "ATLAS_CONFIGURATION_ERROR"


class DataError(AtlasQuantError):
    """Raised when market or application data is invalid."""

    error_code = "ATLAS_DATA_ERROR"


class StrategyError(AtlasQuantError):
    """Raised when strategy processing fails."""

    error_code = "ATLAS_STRATEGY_ERROR"


class BacktestError(AtlasQuantError):
    """Raised when a backtesting operation fails."""

    error_code = "ATLAS_BACKTEST_ERROR"


class RiskError(AtlasQuantError):
    """Raised when a risk validation or control fails."""

    error_code = "ATLAS_RISK_ERROR"