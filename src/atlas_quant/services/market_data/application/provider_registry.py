
"""Application service for managing market data providers."""

import re
from dataclasses import replace

from atlas_quant.platform.errors import AtlasQuantError
from atlas_quant.services.market_data.application.repositories import (
    ProviderRepository,
)
from atlas_quant.services.market_data.domain.providers import (
    MarketDataProvider,
)


class ProviderAlreadyExistsError(AtlasQuantError):
    """Raised when a provider is already registered."""

    error_code = "PROVIDER_ALREADY_EXISTS"
    http_status_code = 409


class ProviderNotFoundError(AtlasQuantError):
    """Raised when a provider cannot be found."""

    error_code = "PROVIDER_NOT_FOUND"
    http_status_code = 404


class ProviderRegistry:
    """Coordinate provider registration and lifecycle operations."""

    def __init__(
        self,
        repository: ProviderRepository,
    ) -> None:
        self._repository = repository

    async def register_provider(
        self,
        provider: MarketDataProvider,
    ) -> MarketDataProvider:
        """Register a new market data provider."""

        existing = await self._repository.get_by_id(
            provider.provider_id
        )

        if existing is not None:
            raise ProviderAlreadyExistsError(
                f"Provider {provider.provider_id} is already registered.",
                details={"provider_id": provider.provider_id},
            )

        await self._repository.add(provider)

        return provider

    async def get_provider(
        self,
        provider_id: str,
    ) -> MarketDataProvider:
        """Retrieve a provider by its identifier."""

        normalized_id = self._normalize_provider_id(provider_id)

        provider = await self._repository.get_by_id(normalized_id)

        if provider is None:
            raise ProviderNotFoundError(
                f"Provider {normalized_id} was not found.",
                details={"provider_id": normalized_id},
            )

        return provider

    async def list_providers(
        self,
    ) -> tuple[MarketDataProvider, ...]:
        """Retrieve all registered providers."""

        return await self._repository.list_all()

    async def enable_provider(
        self,
        provider_id: str,
    ) -> MarketDataProvider:
        """Enable a registered provider."""

        provider = await self.get_provider(provider_id)

        if provider.is_enabled:
            return provider

        enabled_provider = replace(provider, is_enabled=True)

        await self._repository.update(enabled_provider)

        return enabled_provider

    async def disable_provider(
        self,
        provider_id: str,
    ) -> MarketDataProvider:
        """Disable a registered provider."""

        provider = await self.get_provider(provider_id)

        if not provider.is_enabled:
            return provider

        disabled_provider = replace(provider, is_enabled=False)

        await self._repository.update(disabled_provider)

        return disabled_provider

    @staticmethod
    def _normalize_provider_id(provider_id: str) -> str:
        """Normalize and validate a provider identifier."""

        if not isinstance(provider_id, str):
            raise TypeError("provider_id must be a string.")

        normalized_id = provider_id.strip().lower()

        if not normalized_id:
            raise ValueError("provider_id cannot be empty.")

        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", normalized_id):
            raise ValueError(
                "provider_id must contain only lowercase letters, "
                "numbers, hyphens, and underscores."
            )

        return normalized_id