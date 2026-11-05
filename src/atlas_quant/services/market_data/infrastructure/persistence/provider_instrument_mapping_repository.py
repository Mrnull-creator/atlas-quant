"""Asynchronous SQLAlchemy repository for provider instrument mappings."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from atlas_quant.services.market_data.application.repository_errors import (
    ProviderInstrumentMappingConflictError,
    ProviderInstrumentMappingRecordNotFoundError,
)
from atlas_quant.services.market_data.domain.provider_instrument_mappings import (
    ProviderInstrumentMapping,
)
from atlas_quant.services.market_data.infrastructure.persistence.provider_instrument_mapping import (
    ProviderInstrumentMappingORM,
)


ACTIVE_IDENTIFIER_INDEX = (
    "uq_provider_instrument_mappings_active_provider_identifier"
)


class SQLAlchemyProviderInstrumentMappingRepository:
    """Persist provider-specific instrument identifiers."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session_factory = session_factory

    async def get_by_id(
        self,
        mapping_id: UUID,
    ) -> ProviderInstrumentMapping | None:
        """Retrieve a mapping by its permanent UUID."""

        async with self._session_factory() as session:
            orm_mapping = await session.get(
                ProviderInstrumentMappingORM,
                mapping_id,
            )

            if orm_mapping is None:
                return None

            return self._to_domain(orm_mapping)

    async def get_by_provider_identifier(
        self,
        provider_id: str,
        provider_symbol: str,
        provider_exchange: str | None = None,
    ) -> ProviderInstrumentMapping | None:
        """Retrieve the active mapping for a provider identifier."""

        normalized_provider_id = provider_id.strip().lower()
        normalized_symbol = provider_symbol.strip()
        normalized_exchange = (
            provider_exchange.strip()
            if provider_exchange is not None
            else ""
        )

        statement = select(
            ProviderInstrumentMappingORM
        ).where(
            ProviderInstrumentMappingORM.provider_id
            == normalized_provider_id,
            ProviderInstrumentMappingORM.provider_symbol
            == normalized_symbol,
            ProviderInstrumentMappingORM.provider_exchange
            == normalized_exchange,
            ProviderInstrumentMappingORM.is_active.is_(True),
        )

        async with self._session_factory() as session:
            result = await session.execute(statement)
            orm_mapping = result.scalar_one_or_none()

            if orm_mapping is None:
                return None

            return self._to_domain(orm_mapping)

    async def list_by_instrument_id(
        self,
        instrument_id: UUID,
    ) -> tuple[ProviderInstrumentMapping, ...]:
        """Retrieve active and inactive mappings for an instrument."""

        statement = (
            select(ProviderInstrumentMappingORM)
            .where(
                ProviderInstrumentMappingORM.instrument_id
                == instrument_id
            )
            .order_by(
                ProviderInstrumentMappingORM.provider_id,
                ProviderInstrumentMappingORM.provider_symbol,
                ProviderInstrumentMappingORM.provider_exchange,
                ProviderInstrumentMappingORM.mapping_id,
            )
        )

        async with self._session_factory() as session:
            result = await session.execute(statement)
            mappings = result.scalars().all()

        return tuple(self._to_domain(item) for item in mappings)

    async def list_by_provider_id(
        self,
        provider_id: str,
    ) -> tuple[ProviderInstrumentMapping, ...]:
        """Retrieve active and inactive mappings for a provider."""

        normalized_provider_id = provider_id.strip().lower()

        statement = (
            select(ProviderInstrumentMappingORM)
            .where(
                ProviderInstrumentMappingORM.provider_id
                == normalized_provider_id
            )
            .order_by(
                ProviderInstrumentMappingORM.provider_symbol,
                ProviderInstrumentMappingORM.provider_exchange,
                ProviderInstrumentMappingORM.mapping_id,
            )
        )

        async with self._session_factory() as session:
            result = await session.execute(statement)
            mappings = result.scalars().all()

        return tuple(self._to_domain(item) for item in mappings)

    async def add(
        self,
        mapping: ProviderInstrumentMapping,
    ) -> None:
        """Insert a new provider instrument mapping."""

        try:
            async with self._session_factory() as session:
                async with session.begin():
                    session.add(self._to_orm(mapping))
                    await session.flush()

        except IntegrityError as exc:
            if self._is_active_identifier_conflict(exc):
                raise ProviderInstrumentMappingConflictError(
                    "An active mapping already uses this provider identifier."
                ) from exc

            raise

    async def update(
        self,
        mapping: ProviderInstrumentMapping,
    ) -> None:
        """Update mapping status without changing its historical identity."""

        try:
            async with self._session_factory() as session:
                async with session.begin():
                    orm_mapping = await session.get(
                        ProviderInstrumentMappingORM,
                        mapping.mapping_id,
                    )

                    if orm_mapping is None:
                        raise ProviderInstrumentMappingRecordNotFoundError(
                            f"Mapping {mapping.mapping_id} was not found."
                        )

                    existing_identity = (
                        orm_mapping.instrument_id,
                        orm_mapping.provider_id,
                        orm_mapping.provider_symbol,
                        orm_mapping.provider_exchange or None,
                    )

                    requested_identity = (
                        mapping.instrument_id,
                        mapping.provider_id,
                        mapping.provider_symbol,
                        mapping.provider_exchange,
                    )

                    if existing_identity != requested_identity:
                        raise ValueError(
                            "Mapping identity is immutable. "
                            "Deactivate the existing mapping and create "
                            "a new mapping instead."
                        )

                    orm_mapping.is_active = mapping.is_active
                    await session.flush()

        except IntegrityError as exc:
            if self._is_active_identifier_conflict(exc):
                raise ProviderInstrumentMappingConflictError(
                    "An active mapping already uses this provider identifier."
                ) from exc

            raise

    @staticmethod
    def _to_domain(
        orm_mapping: ProviderInstrumentMappingORM,
    ) -> ProviderInstrumentMapping:
        """Convert a persistence model into a domain mapping."""

        return ProviderInstrumentMapping(
            mapping_id=orm_mapping.mapping_id,
            instrument_id=orm_mapping.instrument_id,
            provider_id=orm_mapping.provider_id,
            provider_symbol=orm_mapping.provider_symbol,
            provider_exchange=orm_mapping.provider_exchange or None,
            is_active=orm_mapping.is_active,
        )

    @staticmethod
    def _to_orm(
        mapping: ProviderInstrumentMapping,
    ) -> ProviderInstrumentMappingORM:
        """Convert a domain mapping into a persistence model."""

        return ProviderInstrumentMappingORM(
            mapping_id=mapping.mapping_id,
            instrument_id=mapping.instrument_id,
            provider_id=mapping.provider_id,
            provider_symbol=mapping.provider_symbol,
            provider_exchange=mapping.provider_exchange or "",
            is_active=mapping.is_active,
        )

    @staticmethod
    def _is_active_identifier_conflict(
        exc: IntegrityError,
    ) -> bool:
        """Identify only active provider-identifier uniqueness violations."""

        diagnostic = getattr(exc.orig, "diag", None)
        constraint_name = getattr(
            diagnostic,
            "constraint_name",
            None,
        )

        if constraint_name == ACTIVE_IDENTIFIER_INDEX:
            return True

        message = str(exc.orig)

        return (
            "UNIQUE constraint failed: "
            "provider_instrument_mappings.provider_id, "
            "provider_instrument_mappings.provider_symbol, "
            "provider_instrument_mappings.provider_exchange"
        ) in message