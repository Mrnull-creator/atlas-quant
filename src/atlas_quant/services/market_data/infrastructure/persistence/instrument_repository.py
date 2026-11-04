"""Asynchronous SQLAlchemy repository for financial instruments."""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from atlas_quant.services.market_data.application.repository_errors import (
    InstrumentBusinessKeyConflictError,
    InstrumentRepositoryRecordNotFoundError,
)
from atlas_quant.services.market_data.domain.enums import AssetClass
from atlas_quant.services.market_data.domain.instruments import Instrument
from atlas_quant.services.market_data.infrastructure.persistence.instrument import (
    InstrumentORM,
)


BUSINESS_KEY_CONSTRAINT = "uq_instruments_business_key"


class SQLAlchemyInstrumentRepository:
    """Persist instruments using asynchronous SQLAlchemy sessions."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session_factory = session_factory

    async def get_by_id(
        self,
        instrument_id: UUID,
    ) -> Instrument | None:
        """Retrieve an instrument by its permanent UUID."""

        async with self._session_factory() as session:
            orm_instrument = await session.get(
                InstrumentORM,
                instrument_id,
            )

            if orm_instrument is None:
                return None

            return self._to_domain(orm_instrument)

    async def get_by_business_key(
        self,
        symbol: str,
        asset_class: AssetClass,
        exchange: str | None,
    ) -> Instrument | None:
        """Retrieve an instrument by its complete business key."""

        normalized_symbol = symbol.strip().upper()
        normalized_exchange = (
            exchange.strip().upper()
            if exchange is not None
            else None
        )

        statement = select(InstrumentORM).where(
            InstrumentORM.symbol == normalized_symbol,
            InstrumentORM.asset_class == asset_class,
        )

        if normalized_exchange is None:
            statement = statement.where(
                InstrumentORM.exchange.is_(None)
            )
        else:
            statement = statement.where(
                InstrumentORM.exchange == normalized_exchange
            )

        async with self._session_factory() as session:
            result = await session.execute(statement)
            orm_instrument = result.scalar_one_or_none()

            if orm_instrument is None:
                return None

            return self._to_domain(orm_instrument)

    async def list_all(self) -> tuple[Instrument, ...]:
        """Retrieve all instruments in deterministic UUID order."""

        statement = select(InstrumentORM).order_by(
            InstrumentORM.instrument_id
        )

        async with self._session_factory() as session:
            result = await session.execute(statement)
            orm_instruments: Sequence[InstrumentORM] = (
                result.scalars().all()
            )

            return tuple(
                self._to_domain(item)
                for item in orm_instruments
            )

    async def add(
        self,
        instrument: Instrument,
    ) -> None:
        """Persist a new instrument."""

        try:
            async with self._session_factory() as session:
                async with session.begin():
                    session.add(self._to_orm(instrument))
                    await session.flush()

        except IntegrityError as exc:
            if self._is_business_key_conflict(exc):
                raise InstrumentBusinessKeyConflictError(
                    "An instrument with this business key already exists."
                ) from exc

            raise

    async def update(
        self,
        instrument: Instrument,
    ) -> None:
        """Update an existing instrument without changing its UUID."""

        try:
            async with self._session_factory() as session:
                async with session.begin():
                    orm_instrument = await session.get(
                        InstrumentORM,
                        instrument.instrument_id,
                    )

                    if orm_instrument is None:
                        raise InstrumentRepositoryRecordNotFoundError(
                            "The instrument to update does not exist."
                        )

                    self._copy_to_orm(instrument, orm_instrument)
                    await session.flush()

        except IntegrityError as exc:
            if self._is_business_key_conflict(exc):
                raise InstrumentBusinessKeyConflictError(
                    "An instrument with this business key already exists."
                ) from exc

            raise

    @staticmethod
    def _to_domain(orm_instrument: InstrumentORM) -> Instrument:
        """Convert a persistence model into a domain instrument."""

        return Instrument(
            instrument_id=orm_instrument.instrument_id,
            symbol=orm_instrument.symbol,
            asset_class=orm_instrument.asset_class,
            exchange=orm_instrument.exchange,
            base_currency=orm_instrument.base_currency,
            quote_currency=orm_instrument.quote_currency,
            price_precision=orm_instrument.price_precision,
            quantity_precision=orm_instrument.quantity_precision,
            tick_size=orm_instrument.tick_size,
            contract_size=orm_instrument.contract_size,
            is_active=orm_instrument.is_active,
        )

    @staticmethod
    def _to_orm(instrument: Instrument) -> InstrumentORM:
        """Convert a domain instrument into a persistence model."""

        return InstrumentORM(
            instrument_id=instrument.instrument_id,
            symbol=instrument.symbol,
            asset_class=instrument.asset_class,
            exchange=instrument.exchange,
            base_currency=instrument.base_currency,
            quote_currency=instrument.quote_currency,
            price_precision=instrument.price_precision,
            quantity_precision=instrument.quantity_precision,
            tick_size=instrument.tick_size,
            contract_size=instrument.contract_size,
            is_active=instrument.is_active,
        )

    @staticmethod
    def _copy_to_orm(
        instrument: Instrument,
        orm_instrument: InstrumentORM,
    ) -> None:
        """Copy mutable domain attributes without changing database identity."""

        orm_instrument.symbol = instrument.symbol
        orm_instrument.asset_class = instrument.asset_class
        orm_instrument.exchange = instrument.exchange
        orm_instrument.base_currency = instrument.base_currency
        orm_instrument.quote_currency = instrument.quote_currency
        orm_instrument.price_precision = instrument.price_precision
        orm_instrument.quantity_precision = instrument.quantity_precision
        orm_instrument.tick_size = instrument.tick_size
        orm_instrument.contract_size = instrument.contract_size
        orm_instrument.is_active = instrument.is_active

    @staticmethod
    def _is_business_key_conflict(exc: IntegrityError) -> bool:
        """Identify the instrument business-key uniqueness constraint."""

        diagnostic = getattr(exc.orig, "diag", None)
        constraint_name = getattr(
            diagnostic,
            "constraint_name",
            None,
        )

        if constraint_name == BUSINESS_KEY_CONSTRAINT:
            return True

        # SQLite does not expose PostgreSQL constraint diagnostics.
        sqlite_message = str(exc.orig)

        return (
            "UNIQUE constraint failed: "
            "instruments.symbol, instruments.asset_class, instruments.exchange"
        ) in sqlite_message
