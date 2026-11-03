
from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from atlas_quant.services.market_data.domain.enums import AssetClass
from atlas_quant.services.market_data.domain.instruments import Instrument


def test_create_forex_instrument():
    instrument = Instrument(
        symbol="EURUSD",
        asset_class=AssetClass.FOREX,
        base_currency="EUR",
        quote_currency="USD",
        tick_size=Decimal("0.00001"),
    )

    assert instrument.symbol == "EURUSD"
    assert instrument.asset_class == AssetClass.FOREX
    assert instrument.base_currency == "EUR"
    assert instrument.quote_currency == "USD"
    assert instrument.tick_size == Decimal("0.00001")


def test_instrument_normalizes_identifiers():
    instrument = Instrument(
        symbol=" eurusd ",
        asset_class=AssetClass.FOREX,
        base_currency=" eur ",
        quote_currency=" usd ",
    )

    assert instrument.symbol == "EURUSD"
    assert instrument.base_currency == "EUR"
    assert instrument.quote_currency == "USD"


def test_instrument_normalizes_exchange():
    instrument = Instrument(
        symbol="aapl",
        asset_class=AssetClass.EQUITY,
        exchange=" nasdaq ",
    )

    assert instrument.exchange == "NASDAQ"


def test_instrument_rejects_empty_symbol():
    with pytest.raises(ValueError, match="cannot be empty"):
        Instrument(
            symbol=" ",
            asset_class=AssetClass.FOREX,
        )


def test_instrument_rejects_invalid_asset_class():
    with pytest.raises(TypeError, match="AssetClass"):
        Instrument(
            symbol="EURUSD",
            asset_class="forex",
        )


def test_instrument_rejects_negative_precision():
    with pytest.raises(ValueError, match="cannot be negative"):
        Instrument(
            symbol="EURUSD",
            asset_class=AssetClass.FOREX,
            price_precision=-1,
        )


def test_instrument_rejects_float_tick_size():
    with pytest.raises(TypeError, match="Decimal"):
        Instrument(
            symbol="EURUSD",
            asset_class=AssetClass.FOREX,
            tick_size=0.00001,
        )


def test_instrument_rejects_non_positive_tick_size():
    with pytest.raises(ValueError, match="finite positive"):
        Instrument(
            symbol="EURUSD",
            asset_class=AssetClass.FOREX,
            tick_size=Decimal("0"),
        )


def test_instrument_is_immutable():
    instrument = Instrument(
        symbol="EURUSD",
        asset_class=AssetClass.FOREX,
    )

    with pytest.raises(FrozenInstanceError):
        instrument.symbol = "GBPUSD"