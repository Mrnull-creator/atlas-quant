"""Tests for provider-specific instrument mapping domain models."""

from uuid import UUID, uuid4

import pytest

from atlas_quant.services.market_data.domain.provider_instrument_mappings import (
    ProviderInstrumentMapping,
)


def make_mapping(**overrides) -> ProviderInstrumentMapping:
    values = {
        "instrument_id": uuid4(),
        "provider_id": "dukascopy",
        "provider_symbol": "EUR/USD",
        "provider_exchange": None,
    }
    values.update(overrides)

    return ProviderInstrumentMapping(**values)


def test_mapping_generates_uuid_identity():
    mapping = make_mapping()

    assert isinstance(mapping.mapping_id, UUID)


def test_mapping_preserves_supplied_uuid_identity():
    mapping_id = uuid4()

    mapping = make_mapping(mapping_id=mapping_id)

    assert mapping.mapping_id == mapping_id


def test_mapping_normalizes_provider_id():
    mapping = make_mapping(provider_id=" DUKASCOPY ")

    assert mapping.provider_id == "dukascopy"


def test_mapping_trims_provider_symbol_without_changing_case():
    mapping = make_mapping(provider_symbol="  EurUsd  ")

    assert mapping.provider_symbol == "EurUsd"


def test_mapping_trims_provider_exchange_without_changing_case():
    mapping = make_mapping(provider_exchange="  MyExchange  ")

    assert mapping.provider_exchange == "MyExchange"


def test_mapping_allows_missing_provider_exchange():
    mapping = make_mapping(provider_exchange=None)

    assert mapping.provider_exchange is None


def test_provider_identifier_contains_complete_external_identity():
    mapping = make_mapping(
        provider_id=" DUKASCOPY ",
        provider_symbol=" EUR/USD ",
        provider_exchange=" FX ",
    )

    assert mapping.provider_identifier == (
        "dukascopy",
        "EUR/USD",
        "FX",
    )


def test_mapping_defaults_to_active():
    mapping = make_mapping()

    assert mapping.is_active is True


def test_mapping_can_be_inactive():
    mapping = make_mapping(is_active=False)

    assert mapping.is_active is False


@pytest.mark.parametrize(
    "provider_id",
    [
        "",
        "   ",
        "-invalid",
        "invalid provider",
        "invalid.provider",
    ],
)
def test_mapping_rejects_invalid_provider_id(provider_id):
    with pytest.raises(ValueError):
        make_mapping(provider_id=provider_id)


@pytest.mark.parametrize(
    "provider_symbol",
    [
        "",
        "   ",
    ],
)
def test_mapping_rejects_empty_provider_symbol(provider_symbol):
    with pytest.raises(ValueError):
        make_mapping(provider_symbol=provider_symbol)


def test_mapping_rejects_empty_provider_exchange():
    with pytest.raises(ValueError):
        make_mapping(provider_exchange="   ")


def test_mapping_rejects_non_uuid_instrument_identity():
    with pytest.raises(TypeError):
        make_mapping(instrument_id="not-a-uuid")


def test_mapping_rejects_non_boolean_active_status():
    with pytest.raises(TypeError):
        make_mapping(is_active=1)


def test_mapping_is_immutable():
    mapping = make_mapping()

    with pytest.raises(AttributeError):
        mapping.is_active = False