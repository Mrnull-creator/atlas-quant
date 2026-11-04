"""Application service for managing financial instruments."""

from dataclasses import replace
from uuid import UUID

from atlas_quant.platform.errors import AtlasQuantError
from atlas_quant.services.market_data.application.repositories import (
    InstrumentRepository,
)
from atlas_quant.services.market_data.domain.enums import AssetClass
from atlas_quant.services.market_data.domain.instruments import Instrument


class InstrumentAlreadyExistsError(AtlasQuantError):
    """Raised when an instrument business key is already registered."""

    error_code = "INSTRUMENT_ALREADY_EXISTS"
    http_status_code = 409


class InstrumentNotFoundError(AtlasQuantError):
    """Raised when an instrument cannot be found."""

    error_code = "INSTRUMENT_NOT_FOUND"
    http_status_code = 404


class InstrumentRegistry:
    """Coordinate instrument registration and lifecycle operations."""

    def __init__(
        self,
        repository: InstrumentRepository,
    ) -> None:
        self._repository = repository

    async def register_instrument(
        self,
        instrument: Instrument,
    ) -> Instrument:
        """Register a new instrument."""

        existing = await self._repository.get_by_business_key(
            *instrument.business_key
        )

        if existing is not None:
            raise InstrumentAlreadyExistsError(
                "An instrument with this business key is already registered.",
                details={
                    "symbol": instrument.symbol,
                    "asset_class": instrument.asset_class.value,
                    "exchange": instrument.exchange,
                },
            )

        await self._repository.add(instrument)

        return instrument

    async def get_instrument(
        self,
        instrument_id: UUID,
    ) -> Instrument:
        """Retrieve an instrument by its permanent UUID."""

        if not isinstance(instrument_id, UUID):
            raise TypeError("instrument_id must be a UUID.")

        instrument = await self._repository.get_by_id(instrument_id)

        if instrument is None:
            raise InstrumentNotFoundError(
                f"Instrument {instrument_id} was not found.",
                details={"instrument_id": str(instrument_id)},
            )

        return instrument

    async def get_instrument_by_business_key(
        self,
        symbol: str,
        asset_class: AssetClass,
        exchange: str | None = None,
    ) -> Instrument:
        """Retrieve an instrument using its complete business key."""

        normalized_symbol = self._normalize_symbol(symbol)

        if not isinstance(asset_class, AssetClass):
            raise TypeError("asset_class must be an AssetClass value.")

        normalized_exchange = self._normalize_exchange(exchange)

        instrument = await self._repository.get_by_business_key(
            normalized_symbol,
            asset_class,
            normalized_exchange,
        )

        if instrument is None:
            raise InstrumentNotFoundError(
                "No instrument matches the supplied business key.",
                details={
                    "symbol": normalized_symbol,
                    "asset_class": asset_class.value,
                    "exchange": normalized_exchange,
                },
            )

        return instrument

    async def list_instruments(self) -> tuple[Instrument, ...]:
        """Retrieve all registered instruments."""

        return await self._repository.list_all()

    async def update_instrument(
        self,
        instrument: Instrument,
    ) -> Instrument:
        """Update an existing instrument without changing its identity."""

        existing = await self._repository.get_by_id(
            instrument.instrument_id
        )

        if existing is None:
            raise InstrumentNotFoundError(
                f"Instrument {instrument.instrument_id} was not found.",
                details={
                    "instrument_id": str(instrument.instrument_id),
                },
            )

        conflicting = await self._repository.get_by_business_key(
            *instrument.business_key
        )

        if (
            conflicting is not None
            and conflicting.instrument_id != instrument.instrument_id
        ):
            raise InstrumentAlreadyExistsError(
                "Another instrument already uses this business key.",
                details={
                    "symbol": instrument.symbol,
                    "asset_class": instrument.asset_class.value,
                    "exchange": instrument.exchange,
                },
            )

        await self._repository.update(instrument)

        return instrument

    async def deactivate_instrument(
        self,
        instrument_id: UUID,
    ) -> Instrument:
        """Deactivate an instrument without deleting its history."""

        instrument = await self.get_instrument(instrument_id)

        if not instrument.is_active:
            return instrument

        deactivated = replace(instrument, is_active=False)

        await self._repository.update(deactivated)

        return deactivated

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        """Normalize and validate an instrument symbol."""

        if not isinstance(symbol, str):
            raise TypeError("Instrument symbol must be a string.")

        normalized_symbol = symbol.strip().upper()

        if not normalized_symbol:
            raise ValueError("Instrument symbol cannot be empty.")

        return normalized_symbol

    @staticmethod
    def _normalize_exchange(exchange: str | None) -> str | None:
        """Normalize and validate an optional exchange."""

        if exchange is None:
            return None

        if not isinstance(exchange, str):
            raise TypeError("Exchange must be a string.")

        normalized_exchange = exchange.strip().upper()

        if not normalized_exchange:
            raise ValueError("Exchange cannot be empty.")

        return normalized_exchange