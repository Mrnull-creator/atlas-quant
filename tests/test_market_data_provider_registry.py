
import asyncio

import pytest

from atlas_quant.services.market_data.application.provider_registry import (
    ProviderAlreadyExistsError,
    ProviderNotFoundError,
    ProviderRegistry,
)
from atlas_quant.services.market_data.domain.enums import (
    AssetClass,
    Timeframe,
)
from atlas_quant.services.market_data.domain.providers import (
    MarketDataProvider,
)


class InMemoryProviderRepository:
    """In-memory repository for provider registry tests."""

    def __init__(self) -> None:
        self.providers: dict[str, MarketDataProvider] = {}

    async def get_by_id(
        self,
        provider_id: str,
    ) -> MarketDataProvider | None:
        return self.providers.get(provider_id)

    async def list_all(
        self,
    ) -> tuple[MarketDataProvider, ...]:
        return tuple(self.providers.values())

    async def add(
        self,
        provider: MarketDataProvider,
    ) -> None:
        if provider.provider_id in self.providers:
            raise ValueError("Duplicate provider.")

        self.providers[provider.provider_id] = provider

    async def update(
        self,
        provider: MarketDataProvider,
    ) -> None:
        if provider.provider_id not in self.providers:
            raise ValueError("Provider does not exist.")

        self.providers[provider.provider_id] = provider


def create_provider(
    provider_id: str = "dukascopy",
    *,
    is_enabled: bool = True,
) -> MarketDataProvider:
    return MarketDataProvider(
        provider_id=provider_id,
        name="Dukascopy",
        supported_asset_classes=(AssetClass.FOREX,),
        supported_timeframes=(
            Timeframe.M1,
            Timeframe.M5,
            Timeframe.H1,
        ),
        supports_historical_data=True,
        supports_quote_data=True,
        is_enabled=is_enabled,
    )


def create_registry():
    repository = InMemoryProviderRepository()
    registry = ProviderRegistry(repository)

    return registry, repository


def test_register_provider():
    async def scenario():
        registry, repository = create_registry()
        provider = create_provider()

        result = await registry.register_provider(provider)

        assert result == provider
        assert repository.providers["dukascopy"] == provider

    asyncio.run(scenario())


def test_reject_duplicate_provider():
    async def scenario():
        registry, _ = create_registry()
        provider = create_provider()

        await registry.register_provider(provider)

        with pytest.raises(ProviderAlreadyExistsError):
            await registry.register_provider(provider)

    asyncio.run(scenario())


def test_get_provider():
    async def scenario():
        registry, _ = create_registry()
        provider = create_provider()

        await registry.register_provider(provider)

        result = await registry.get_provider("dukascopy")

        assert result == provider

    asyncio.run(scenario())


def test_get_provider_normalizes_identifier():
    async def scenario():
        registry, _ = create_registry()
        provider = create_provider()

        await registry.register_provider(provider)

        result = await registry.get_provider("  DUKASCOPY  ")

        assert result.provider_id == "dukascopy"

    asyncio.run(scenario())


def test_get_missing_provider():
    async def scenario():
        registry, _ = create_registry()

        with pytest.raises(ProviderNotFoundError):
            await registry.get_provider("dukascopy")

    asyncio.run(scenario())


def test_list_providers():
    async def scenario():
        registry, _ = create_registry()

        dukascopy = create_provider()
        another = MarketDataProvider(
            provider_id="example",
            name="Example Provider",
            supported_asset_classes=(AssetClass.CRYPTO,),
            supports_historical_data=False,
            supports_quote_data=True,
        )

        await registry.register_provider(dukascopy)
        await registry.register_provider(another)

        result = await registry.list_providers()

        assert len(result) == 2
        assert dukascopy in result
        assert another in result

    asyncio.run(scenario())


def test_enable_disabled_provider():
    async def scenario():
        registry, repository = create_registry()
        provider = create_provider(is_enabled=False)

        await registry.register_provider(provider)

        result = await registry.enable_provider("dukascopy")

        assert result.is_enabled is True
        assert repository.providers["dukascopy"].is_enabled is True

    asyncio.run(scenario())


def test_enable_provider_is_idempotent():
    async def scenario():
        registry, repository = create_registry()
        provider = create_provider()

        await registry.register_provider(provider)

        result = await registry.enable_provider("dukascopy")

        assert result == provider
        assert repository.providers["dukascopy"] == provider

    asyncio.run(scenario())


def test_disable_enabled_provider():
    async def scenario():
        registry, repository = create_registry()
        provider = create_provider()

        await registry.register_provider(provider)

        result = await registry.disable_provider("dukascopy")

        assert result.is_enabled is False
        assert repository.providers["dukascopy"].is_enabled is False

    asyncio.run(scenario())


def test_disable_provider_is_idempotent():
    async def scenario():
        registry, repository = create_registry()
        provider = create_provider(is_enabled=False)

        await registry.register_provider(provider)

        result = await registry.disable_provider("dukascopy")

        assert result == provider
        assert repository.providers["dukascopy"] == provider

    asyncio.run(scenario())


def test_enable_missing_provider():
    async def scenario():
        registry, _ = create_registry()

        with pytest.raises(ProviderNotFoundError):
            await registry.enable_provider("dukascopy")

    asyncio.run(scenario())


def test_disable_missing_provider():
    async def scenario():
        registry, _ = create_registry()

        with pytest.raises(ProviderNotFoundError):
            await registry.disable_provider("dukascopy")

    asyncio.run(scenario())


def test_reject_empty_provider_id():
    async def scenario():
        registry, _ = create_registry()

        with pytest.raises(ValueError):
            await registry.get_provider("  ")

    asyncio.run(scenario())


def test_reject_invalid_provider_id():
    async def scenario():
        registry, _ = create_registry()

        with pytest.raises(ValueError):
            await registry.get_provider("Dukascopy API")

    asyncio.run(scenario())


def test_reject_non_string_provider_id():
    async def scenario():
        registry, _ = create_registry()

        with pytest.raises(TypeError):
            await registry.get_provider(None)

    asyncio.run(scenario())


def test_enable_provider_preserves_other_metadata():
    async def scenario():
        registry, _ = create_registry()
        provider = create_provider(is_enabled=False)

        await registry.register_provider(provider)

        result = await registry.enable_provider("dukascopy")

        assert result.name == provider.name
        assert result.supported_asset_classes == (
            provider.supported_asset_classes
        )
        assert result.supported_timeframes == (
            provider.supported_timeframes
        )
        assert result.supports_historical_data is True
        assert result.supports_quote_data is True

    asyncio.run(scenario())


def test_provider_model_remains_immutable():
    async def scenario():
        registry, _ = create_registry()
        provider = create_provider()

        await registry.register_provider(provider)

        result = await registry.disable_provider("dukascopy")

        assert provider.is_enabled is True
        assert result.is_enabled is False

    asyncio.run(scenario())