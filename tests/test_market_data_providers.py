import pytest

from atlas_quant.services.market_data.domain.enums import (
    AssetClass,
    Timeframe,
)
from atlas_quant.services.market_data.domain.providers import (
    MarketDataProvider,
)


def create_provider(**overrides) -> MarketDataProvider:
    values = {
        "provider_id": "dukascopy",
        "name": "Dukascopy",
        "supported_asset_classes": (AssetClass.FOREX,),
        "supported_timeframes": (
            Timeframe.M1,
            Timeframe.M5,
            Timeframe.H1,
        ),
    }

    values.update(overrides)

    return MarketDataProvider(**values)


def test_create_valid_provider():
    provider = create_provider()

    assert provider.provider_id == "dukascopy"
    assert provider.name == "Dukascopy"
    assert provider.supports_historical_data is True
    assert provider.supports_quote_data is False
    assert provider.is_enabled is True


def test_normalize_provider_id():
    provider = create_provider(provider_id="  DUKASCOPY  ")

    assert provider.provider_id == "dukascopy"


def test_normalize_provider_name():
    provider = create_provider(name="  Dukascopy Bank  ")

    assert provider.name == "Dukascopy Bank"


def test_reject_empty_provider_id():
    with pytest.raises(ValueError):
        create_provider(provider_id="  ")


def test_reject_invalid_provider_id():
    with pytest.raises(ValueError):
        create_provider(provider_id="Dukascopy API")


def test_reject_empty_provider_name():
    with pytest.raises(ValueError):
        create_provider(name="  ")


def test_reject_empty_asset_classes():
    with pytest.raises(ValueError):
        create_provider(supported_asset_classes=())


def test_reject_invalid_asset_class():
    with pytest.raises(TypeError):
        create_provider(supported_asset_classes=("forex",))


def test_reject_duplicate_asset_classes():
    with pytest.raises(ValueError):
        create_provider(
            supported_asset_classes=(
                AssetClass.FOREX,
                AssetClass.FOREX,
            )
        )


def test_reject_duplicate_timeframes():
    with pytest.raises(ValueError):
        create_provider(
            supported_timeframes=(
                Timeframe.M1,
                Timeframe.M1,
            )
        )


def test_reject_historical_provider_without_timeframes():
    with pytest.raises(ValueError):
        create_provider(supported_timeframes=())


def test_allow_quote_only_provider():
    provider = create_provider(
        supported_timeframes=(),
        supports_historical_data=False,
        supports_quote_data=True,
    )

    assert provider.supports_quote_data is True
    assert provider.supports_historical_data is False


def test_reject_provider_without_capabilities():
    with pytest.raises(ValueError):
        create_provider(
            supports_historical_data=False,
            supports_quote_data=False,
            supported_timeframes=(),
        )


def test_reject_timeframes_without_historical_support():
    with pytest.raises(ValueError):
        create_provider(
            supports_historical_data=False,
            supports_quote_data=True,
        )


def test_reject_non_boolean_enabled_flag():
    with pytest.raises(TypeError):
        create_provider(is_enabled=1)


def test_provider_collections_are_immutable():
    asset_classes = [AssetClass.FOREX]
    timeframes = [Timeframe.M1]

    provider = create_provider(
        supported_asset_classes=asset_classes,
        supported_timeframes=timeframes,
    )

    assert isinstance(provider.supported_asset_classes, tuple)
    assert isinstance(provider.supported_timeframes, tuple)


def test_provider_is_frozen():
    provider = create_provider()

    with pytest.raises(AttributeError):
        provider.name = "Changed"