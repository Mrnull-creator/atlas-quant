
import asyncio
from decimal import Decimal

import pytest

from atlas_quant.services.market_data.application.instrument_registry import (
    InstrumentAlreadyExistsError,
    InstrumentNotFoundError,
    InstrumentRegistry,
)
from atlas_quant.services.market_data.domain.enums import AssetClass
from atlas_quant.services.market_data.domain.instruments import Instrument


class InMemoryInstrumentRepository:
    """In-memory repository for instrument registry tests."""

    def __init__(self) -> None:
        self.instruments: dict[str, Instrument] = {}

    async def get_by_symbol(
        self,
        symbol: str,
    ) -> Instrument | None:
        return self.instruments.get(symbol)

    async def list_all(self) -> tuple[Instrument, ...]:
        return tuple(self.instruments.values())

    async def add(self, instrument: Instrument) -> None:
        if instrument.symbol in self.instruments:
            raise ValueError("Duplicate instrument.")

        self.instruments[instrument.symbol] = instrument

    async def update(self, instrument: Instrument) -> None:
        if instrument.symbol not in self.instruments:
            raise ValueError("Instrument does not exist.")

        self.instruments[instrument.symbol] = instrument


def create_instrument(
    symbol: str = "EURUSD",
    *,
    is_active: bool = True,
) -> Instrument:
    return Instrument(
        symbol=symbol,
        asset_class=AssetClass.FOREX,
        base_currency="EUR",
        quote_currency="USD",
        price_precision=5,
        quantity_precision=2,
        tick_size=Decimal("0.00001"),
        is_active=is_active,
    )


def create_registry():
    repository = InMemoryInstrumentRepository()
    registry = InstrumentRegistry(repository)

    return registry, repository


def test_register_instrument():
    async def scenario():
        registry, repository = create_registry()
        instrument = create_instrument()

        result = await registry.register_instrument(instrument)

        assert result == instrument
        assert repository.instruments["EURUSD"] == instrument

    asyncio.run(scenario())


def test_reject_duplicate_instrument():
    async def scenario():
        registry, _ = create_registry()
        instrument = create_instrument()

        await registry.register_instrument(instrument)

        with pytest.raises(InstrumentAlreadyExistsError):
            await registry.register_instrument(instrument)

    asyncio.run(scenario())


def test_get_instrument():
    async def scenario():
        registry, _ = create_registry()
        instrument = create_instrument()

        await registry.register_instrument(instrument)

        result = await registry.get_instrument("EURUSD")

        assert result == instrument

    asyncio.run(scenario())


def test_get_instrument_normalizes_symbol():
    async def scenario():
        registry, _ = create_registry()
        instrument = create_instrument()

        await registry.register_instrument(instrument)

        result = await registry.get_instrument("  eurusd  ")

        assert result.symbol == "EURUSD"

    asyncio.run(scenario())


def test_get_missing_instrument():
    async def scenario():
        registry, _ = create_registry()

        with pytest.raises(InstrumentNotFoundError):
            await registry.get_instrument("EURUSD")

    asyncio.run(scenario())


def test_list_instruments():
    async def scenario():
        registry, _ = create_registry()

        eurusd = create_instrument("EURUSD")
        gbpusd = create_instrument("GBPUSD")

        await registry.register_instrument(eurusd)
        await registry.register_instrument(gbpusd)

        result = await registry.list_instruments()

        assert len(result) == 2
        assert eurusd in result
        assert gbpusd in result

    asyncio.run(scenario())


def test_update_instrument():
    async def scenario():
        registry, repository = create_registry()

        original = create_instrument()
        await registry.register_instrument(original)

        updated = Instrument(
            symbol="EURUSD",
            asset_class=AssetClass.FOREX,
            base_currency="EUR",
            quote_currency="USD",
            price_precision=6,
            quantity_precision=2,
        )

        result = await registry.update_instrument(updated)

        assert result.price_precision == 6
        assert repository.instruments["EURUSD"] == updated

    asyncio.run(scenario())


def test_update_missing_instrument():
    async def scenario():
        registry, _ = create_registry()

        with pytest.raises(InstrumentNotFoundError):
            await registry.update_instrument(
                create_instrument()
            )

    asyncio.run(scenario())


def test_deactivate_instrument():
    async def scenario():
        registry, repository = create_registry()
        instrument = create_instrument()

        await registry.register_instrument(instrument)

        result = await registry.deactivate_instrument("EURUSD")

        assert result.is_active is False
        assert repository.instruments["EURUSD"].is_active is False

    asyncio.run(scenario())


def test_deactivate_instrument_is_idempotent():
    async def scenario():
        registry, repository = create_registry()

        instrument = create_instrument(is_active=False)
        await registry.register_instrument(instrument)

        result = await registry.deactivate_instrument("EURUSD")

        assert result == instrument
        assert repository.instruments["EURUSD"] == instrument

    asyncio.run(scenario())


def test_deactivate_missing_instrument():
    async def scenario():
        registry, _ = create_registry()

        with pytest.raises(InstrumentNotFoundError):
            await registry.deactivate_instrument("EURUSD")

    asyncio.run(scenario())


def test_reject_empty_symbol():
    async def scenario():
        registry, _ = create_registry()

        with pytest.raises(ValueError):
            await registry.get_instrument("  ")

    asyncio.run(scenario())


def test_reject_non_string_symbol():
    async def scenario():
        registry, _ = create_registry()

        with pytest.raises(TypeError):
            await registry.get_instrument(None)

    asyncio.run(scenario())