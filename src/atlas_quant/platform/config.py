
from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


Environment = Literal["development", "testing", "production"]

TradingMode = Literal[
    "research",
    "backtest",
    "paper",
    "sandbox",
]


class Settings(BaseSettings):
    """Centralized configuration for Atlas Quant."""

    model_config = SettingsConfigDict(
        env_prefix="ATLAS_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application metadata
    environment: Environment = "development"
    service_name: str = "atlas-quant"
    service_version: str = "0.1.0"

    # Trading safety
    trading_mode: TradingMode = "research"

    # Infrastructure configuration
    database_url: SecretStr | None = None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached application settings."""
    return Settings()