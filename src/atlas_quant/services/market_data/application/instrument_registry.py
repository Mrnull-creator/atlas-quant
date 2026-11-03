
"""Application service for managing financial instruments."""

from dataclasses import replace

from atlas_quant.platform.errors import AtlasQuantError
from atlas_quant.services.market_data.application.repositories import (
    InstrumentRepository,
)
from atlas_quant.services.market_data.domain.instruments import Instrument


class InstrumentAlreadyExistsError(AtlasQuantError):
    """Raised when an instrument is already registered."""

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

        existing = await self._repository.get_by_symbol(
            instrument.symbol
        )

        if existing is not None:
            raise InstrumentAlreadyExistsError(
                f"Instrument {instrument.symbol} is already registered.",
                details={"symbol": instrument.symbol},
            )

        await self._repository.add(instrument)

        return instrument

    async def get_instrument(
        self,
        symbol: str,
    ) -> Instrument:
        """Retrieve an instrument by symbol."""

        normalized_symbol = self._normalize_symbol(symbol)

        instrument = await self._repository.get_by_symbol(
            normalized_symbol
        )

        if instrument is None:
            raise InstrumentNotFoundError(
                f"Instrument {normalized_symbol} was not found.",
                details={"symbol": normalized_symbol},
            )

        return instrument

    async def list_instruments(self) -> tuple[Instrument, ...]:
        """Retrieve all registered instruments."""

        return await self._repository.list_all()

    async def update_instrument(
        self,
        instrument: Instrument,
    ) -> Instrument:
        """Update an existing instrument."""

        existing = await self._repository.get_by_symbol(
            instrument.symbol
        )

        if existing is None:
            raise InstrumentNotFoundError(
                f"Instrument {instrument.symbol} was not found.",
                details={"symbol": instrument.symbol},
            )

        await self._repository.update(instrument)

        return instrument

    async def deactivate_instrument(
        self,
        symbol: str,
    ) -> Instrument:
        """Deactivate an instrument without deleting its history."""

        normalized_symbol = self._normalize_symbol(symbol)

        instrument = await self._repository.get_by_symbol(
            normalized_symbol
        )

        if instrument is None:
            raise InstrumentNotFoundError(
                f"Instrument {normalized_symbol} was not found.",
                details={"symbol": normalized_symbol},
            )

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