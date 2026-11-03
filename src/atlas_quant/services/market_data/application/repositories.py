"""Repository interfaces for the Market Data Service.

These interfaces define how application services access domain data.

Implementations belong in the infrastructure layer. The application
layer must not depend on PostgreSQL, SQLAlchemy, or other persistence
technologies.
"""

from typing import Protocol

from atlas_quant.services.market_data.domain.instruments import Instrument
from atlas_quant.services.market_data.domain.providers import (
    MarketDataProvider,
)


class InstrumentRepository(Protocol):
    """Persistence interface for financial instruments."""

    async def get_by_symbol(
        self,
        symbol: str,
    ) -> Instrument | None:
        """Retrieve an instrument by its normalized symbol."""
        ...

    async def list_all(self) -> tuple[Instrument, ...]:
        """Retrieve all registered instruments."""
        ...

    async def add(
        self,
        instrument: Instrument,
    ) -> None:
        """Persist a newly registered instrument."""
        ...

    async def update(
        self,
        instrument: Instrument,
    ) -> None:
        """Update an existing instrument."""
        ...


class ProviderRepository(Protocol):
    """Persistence interface for market data providers."""

    async def get_by_id(
        self,
        provider_id: str,
    ) -> MarketDataProvider | None:
        """Retrieve a provider by its identifier."""
        ...

    async def list_all(self) -> tuple[MarketDataProvider, ...]:
        """Retrieve all registered providers."""
        ...

    async def add(
        self,
        provider: MarketDataProvider,
    ) -> None:
        """Persist a newly registered provider."""
        ...

    async def update(
        self,
        provider: MarketDataProvider,
    ) -> None:
        """Update an existing provider."""
        ...