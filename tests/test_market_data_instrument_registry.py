import asyncio
from dataclasses import replace
from decimal import Decimal
from uuid import UUID

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
        self.instruments: dict[UUID, Instrument] = {}

    async def get_by_id(
        self,
        instrument_id: UUID,
    ) -> Instrument | None:
        return self.instruments.get(instrument_id)

    async def get_by_business_key(
        self,
        symbol: str,
        asset_class: AssetClass,
        exchange: str | None,
    ) -> Instrument | None:
        key = (symbol, asset_class, exchange)

        return next(
            (
                instrument
                for instrument in self.instruments.values()
                if instrument.business_key == key
            ),
            None,
        )

    async def list_all(self) -> tuple[Instrument, ...]:
        return tuple(self.instruments.values())

    async def add(self, instrument: Instrument) -> None:
        if instrument.instrument_id in self.instruments:
            raise ValueError("Duplicate instrument ID.")

        if await self.get_by_business_key(*instrument.business_key):
            raise ValueError("Duplicate instrument business key.")

        self.instruments[instrument.instrument_id] = instrument

    async def update(self, instrument: Instrument) -> None:
        if instrument.instrument_id not in self.instruments:
            raise ValueError("Instrument does not exist.")

        conflicting = await self.get_by_business_key(
            *instrument.business_key
        )

        if (
            conflicting is not None
            and conflicting.instrument_id != instrument.instrument_id
        ):
            raise ValueError("Duplicate instrument business key.")

        self.instruments[instrument.instrument_id] = instrument


def create_instrument(
    symbol: str = "EURUSD",
    *,
    is_active: bool = True,
    exchange: str | None = None,
    asset_class: AssetClass = AssetClass.FOREX,
) -> Instrument:
    return Instrument(
        symbol=symbol,
        asset_class=asset_class,
        exchange=exchange,
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
        assert repository.instruments[instrument.instrument_id] == instrument

    asyncio.run(scenario())


def test_instrument_generates_uuid_identity():
    first = create_instrument()
    second = create_instrument()

    assert isinstance(first.instrument_id, UUID)
    assert first.instrument_id != second.instrument_id


def test_reject_duplicate_business_key():
    async def scenario():
        registry, _ = create_registry()
        first = create_instrument()
        duplicate = create_instrument()

        await registry.register_instrument(first)

        with pytest.raises(InstrumentAlreadyExistsError):
            await registry.register_instrument(duplicate)

    asyncio.run(scenario())


def test_same_symbol_on_different_exchanges_is_allowed():
    async def scenario():
        registry, _ = create_registry()

        first = create_instrument(exchange="NYSE")
        second = create_instrument(exchange="NASDAQ")

        await registry.register_instrument(first)
        await registry.register_instrument(second)

        assert first.instrument_id != second.instrument_id
        assert len(await registry.list_instruments()) == 2

    asyncio.run(scenario())


def test_same_symbol_with_different_asset_classes_is_allowed():
    async def scenario():
        registry, _ = create_registry()

        forex = create_instrument(asset_class=AssetClass.FOREX)
        other = create_instrument(asset_class=AssetClass.CRYPTO)

        await registry.register_instrument(forex)
        await registry.register_instrument(other)

        assert len(await registry.list_instruments()) == 2

    asyncio.run(scenario())


def test_get_instrument_by_id():
    async def scenario():
        registry, _ = create_registry()
        instrument = create_instrument()

        await registry.register_instrument(instrument)

        result = await registry.get_instrument(instrument.instrument_id)

        assert result == instrument

    asyncio.run(scenario())


def test_get_instrument_by_business_key():
    async def scenario():
        registry, _ = create_registry()
        instrument = create_instrument(exchange="NYSE")

        await registry.register_instrument(instrument)

        result = await registry.get_instrument_by_business_key(
            " eurusd ",
            AssetClass.FOREX,
            " nyse ",
        )

        assert result == instrument

    asyncio.run(scenario())


def test_business_key_lookup_distinguishes_exchanges():
    async def scenario():
        registry, _ = create_registry()

        nyse = create_instrument(exchange="NYSE")
        nasdaq = create_instrument(exchange="NASDAQ")

        await registry.register_instrument(nyse)
        await registry.register_instrument(nasdaq)

        result = await registry.get_instrument_by_business_key(
            "EURUSD",
            AssetClass.FOREX,
            "NASDAQ",
        )

        assert result == nasdaq

    asyncio.run(scenario())


def test_business_key_lookup_distinguishes_missing_exchange():
    async def scenario():
        registry, _ = create_registry()

        without_exchange = create_instrument()
        with_exchange = create_instrument(exchange="NYSE")

        await registry.register_instrument(without_exchange)
        await registry.register_instrument(with_exchange)

        result = await registry.get_instrument_by_business_key(
            "EURUSD",
            AssetClass.FOREX,
            None,
        )

        assert result == without_exchange

    asyncio.run(scenario())


def test_get_missing_instrument():
    async def scenario():
        registry, _ = create_registry()

        with pytest.raises(InstrumentNotFoundError):
            await registry.get_instrument(UUID(int=1))

    asyncio.run(scenario())


def test_get_missing_business_key():
    async def scenario():
        registry, _ = create_registry()

        with pytest.raises(InstrumentNotFoundError):
            await registry.get_instrument_by_business_key(
                "EURUSD",
                AssetClass.FOREX,
                "NYSE",
            )

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


def test_update_instrument_preserves_identity():
    async def scenario():
        registry, repository = create_registry()

        original = create_instrument()
        await registry.register_instrument(original)

        updated = replace(original, price_precision=6)

        result = await registry.update_instrument(updated)

        assert result.price_precision == 6
        assert result.instrument_id == original.instrument_id
        assert repository.instruments[original.instrument_id] == updated

    asyncio.run(scenario())


def test_update_rejects_conflicting_business_key():
    async def scenario():
        registry, _ = create_registry()

        first = create_instrument(exchange="NYSE")
        second = create_instrument(exchange="NASDAQ")

        await registry.register_instrument(first)
        await registry.register_instrument(second)

        conflicting_update = replace(
            second,
            exchange="NYSE",
        )

        with pytest.raises(InstrumentAlreadyExistsError):
            await registry.update_instrument(conflicting_update)

    asyncio.run(scenario())


def test_update_missing_instrument():
    async def scenario():
        registry, _ = create_registry()

        with pytest.raises(InstrumentNotFoundError):
            await registry.update_instrument(create_instrument())

    asyncio.run(scenario())


def test_deactivate_instrument():
    async def scenario():
        registry, repository = create_registry()
        instrument = create_instrument()

        await registry.register_instrument(instrument)

        result = await registry.deactivate_instrument(
            instrument.instrument_id
        )

        assert result.is_active is False
        assert (
            repository.instruments[instrument.instrument_id].is_active
            is False
        )

    asyncio.run(scenario())


def test_deactivate_instrument_is_idempotent():
    async def scenario():
        registry, repository = create_registry()

        instrument = create_instrument(is_active=False)
        await registry.register_instrument(instrument)

        result = await registry.deactivate_instrument(
            instrument.instrument_id
        )

        assert result == instrument
        assert repository.instruments[instrument.instrument_id] == instrument

    asyncio.run(scenario())


def test_deactivate_missing_instrument():
    async def scenario():
        registry, _ = create_registry()

        with pytest.raises(InstrumentNotFoundError):
            await registry.deactivate_instrument(UUID(int=1))

    asyncio.run(scenario())


def test_reject_empty_symbol():
    async def scenario():
        registry, _ = create_registry()

        with pytest.raises(ValueError):
            await registry.get_instrument_by_business_key(
                "  ",
                AssetClass.FOREX,
            )

    asyncio.run(scenario())


def test_reject_non_uuid_instrument_id():
    async def scenario():
        registry, _ = create_registry()

        with pytest.raises(TypeError):
            await registry.get_instrument("EURUSD")

    asyncio.run(scenario())