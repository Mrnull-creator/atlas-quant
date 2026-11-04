from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError

from atlas_quant.services.market_data.contracts.instruments import (
    InstrumentContract,
)
from atlas_quant.services.market_data.domain.enums import AssetClass


INSTRUMENT_ID = UUID("12345678-1234-5678-1234-567812345678")


def create_contract(**overrides):
    values = {
        "instrument_id": INSTRUMENT_ID,
        "symbol": "EURUSD",
        "asset_class": AssetClass.FOREX,
        "base_currency": "EUR",
        "quote_currency": "USD",
        "tick_size": Decimal("0.00001"),
    }

    values.update(overrides)
    return InstrumentContract(**values)


def test_create_instrument_contract():
    contract = create_contract()

    assert contract.instrument_id == INSTRUMENT_ID
    assert contract.symbol == "EURUSD"
    assert contract.asset_class == AssetClass.FOREX
    assert contract.base_currency == "EUR"
    assert contract.quote_currency == "USD"


def test_requires_instrument_id():
    with pytest.raises(ValidationError):
        InstrumentContract(
            symbol="EURUSD",
            asset_class=AssetClass.FOREX,
        )


def test_rejects_invalid_instrument_id():
    with pytest.raises(ValidationError):
        create_contract(instrument_id="not-a-uuid")


def test_accepts_uuid_string():
    contract = create_contract(
        instrument_id=str(INSTRUMENT_ID),
    )

    assert contract.instrument_id == INSTRUMENT_ID


def test_normalizes_identifiers():
    contract = create_contract(
        symbol=" eurusd ",
        base_currency=" eur ",
        quote_currency=" usd ",
    )

    assert contract.symbol == "EURUSD"
    assert contract.base_currency == "EUR"
    assert contract.quote_currency == "USD"


def test_rejects_empty_symbol():
    with pytest.raises(ValidationError):
        create_contract(symbol=" ")


def test_rejects_invalid_asset_class():
    with pytest.raises(ValidationError):
        create_contract(asset_class="invalid")


def test_rejects_negative_precision():
    with pytest.raises(ValidationError):
        create_contract(price_precision=-1)


def test_rejects_boolean_precision():
    with pytest.raises(ValidationError):
        create_contract(price_precision=True)


@pytest.mark.parametrize(
    "value",
    [
        Decimal("0"),
        Decimal("-1"),
        Decimal("NaN"),
        Decimal("Infinity"),
    ],
)
def test_rejects_invalid_tick_size(value):
    with pytest.raises(ValidationError):
        create_contract(tick_size=value)


def test_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        create_contract(unexpected_field="value")


def test_contract_is_immutable():
    contract = create_contract()

    with pytest.raises(ValidationError):
        contract.symbol = "GBPUSD"


def test_contract_json_serialization():
    contract = create_contract()

    payload = contract.model_dump_json()

    assert f'"instrument_id":"{INSTRUMENT_ID}"' in payload
    assert '"symbol":"EURUSD"' in payload
    assert '"asset_class":"forex"' in payload
    assert '"tick_size":"0.00001"' in payload


def test_contract_dictionary_serialization():
    contract = create_contract()

    payload = contract.model_dump()

    assert payload["instrument_id"] == INSTRUMENT_ID
    assert payload["symbol"] == "EURUSD"
    assert payload["tick_size"] == Decimal("0.00001")