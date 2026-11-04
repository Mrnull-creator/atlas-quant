"""Asynchronous SQLAlchemy repository for market data providers."""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from atlas_quant.services.market_data.application.repository_errors import (
    ProviderRepositoryDuplicateError,
    ProviderRepositoryRecordNotFoundError,
)
from atlas_quant.services.market_data.domain.enums import (
    AssetClass,
    Timeframe,
)
from atlas_quant.services.market_data.domain.providers import (
    MarketDataProvider,
)
from atlas_quant.services.market_data.infrastructure.persistence.provider import (
    ProviderORM,
)


class SQLAlchemyProviderRepository:
    """Persist market data providers using SQLAlchemy's async ORM."""

    def __init__(
        self,
        session_factory: async_sessionmaker,
    ) -> None:
        self._session_factory = session_factory

    async def get_by_id(
        self,
        provider_id: str,
    ) -> MarketDataProvider | None:
        """Retrieve a provider by its normalized identifier."""

        normalized_id = provider_id.strip().lower()

        async with self._session_factory() as session:
            orm_provider = await session.get(
                ProviderORM,
                normalized_id,
            )

        if orm_provider is None:
            return None

        return self._to_domain(orm_provider)

    async def list_all(
        self,
    ) -> tuple[MarketDataProvider, ...]:
        """Retrieve all providers in deterministic identifier order."""

        statement = select(ProviderORM).order_by(
            ProviderORM.provider_id
        )

        async with self._session_factory() as session:
            result = await session.execute(statement)
            orm_providers = result.scalars().all()

        return tuple(
            self._to_domain(provider)
            for provider in orm_providers
        )

    async def add(
        self,
        provider: MarketDataProvider,
    ) -> None:
        """Insert a new provider."""

        orm_provider = self._to_orm(provider)

        try:
            async with self._session_factory() as session:
                async with session.begin():
                    session.add(orm_provider)
                    await session.flush()
        except IntegrityError as error:
            if self._is_duplicate_provider_id(error):
                raise ProviderRepositoryDuplicateError(
                    f"Provider {provider.provider_id} already exists."
                ) from error

            raise

    async def update(
        self,
        provider: MarketDataProvider,
    ) -> None:
        """Update an existing provider without changing its identifier."""

        async with self._session_factory() as session:
            async with session.begin():
                orm_provider = await session.get(
                    ProviderORM,
                    provider.provider_id,
                )

                if orm_provider is None:
                    raise ProviderRepositoryRecordNotFoundError(
                        f"Provider {provider.provider_id} was not found."
                    )

                orm_provider.name = provider.name
                orm_provider.supported_asset_classes = [
                    asset_class.value
                    for asset_class in provider.supported_asset_classes
                ]
                orm_provider.supported_timeframes = [
                    timeframe.value
                    for timeframe in provider.supported_timeframes
                ]
                orm_provider.supports_historical_data = (
                    provider.supports_historical_data
                )
                orm_provider.supports_quote_data = (
                    provider.supports_quote_data
                )
                orm_provider.is_enabled = provider.is_enabled

                await session.flush()

    @staticmethod
    def _to_domain(
        orm_provider: ProviderORM,
    ) -> MarketDataProvider:
        """Convert a persistence model into a domain provider."""

        return MarketDataProvider(
            provider_id=orm_provider.provider_id,
            name=orm_provider.name,
            supported_asset_classes=tuple(
                AssetClass(value)
                for value in orm_provider.supported_asset_classes
            ),
            supported_timeframes=tuple(
                Timeframe(value)
                for value in orm_provider.supported_timeframes
            ),
            supports_historical_data=(
                orm_provider.supports_historical_data
            ),
            supports_quote_data=orm_provider.supports_quote_data,
            is_enabled=orm_provider.is_enabled,
        )

    @staticmethod
    def _to_orm(
        provider: MarketDataProvider,
    ) -> ProviderORM:
        """Convert a domain provider into a persistence model."""

        return ProviderORM(
            provider_id=provider.provider_id,
            name=provider.name,
            supported_asset_classes=[
                asset_class.value
                for asset_class in provider.supported_asset_classes
            ],
            supported_timeframes=[
                timeframe.value
                for timeframe in provider.supported_timeframes
            ],
            supports_historical_data=(
                provider.supports_historical_data
            ),
            supports_quote_data=provider.supports_quote_data,
            is_enabled=provider.is_enabled,
        )

    @staticmethod
    def _is_duplicate_provider_id(
        error: IntegrityError,
    ) -> bool:
        """Identify only provider primary-key uniqueness violations."""

        original_error = error.orig

        diagnostic = getattr(original_error, "diag", None)
        constraint_name = getattr(
            diagnostic,
            "constraint_name",
            None,
        )

        if constraint_name == "pk_market_data_providers":
            return True

        message = str(original_error).lower()

        return (
            "market_data_providers.provider_id" in message
            and (
                "unique constraint failed" in message
                or "primary key constraint failed" in message
            )
        )
