"""Tests for market data provider adapter contracts."""

from datetime import datetime, timedelta, timezone
from io import BytesIO, StringIO
from uuid import uuid4

import pytest

from atlas_quant.services.market_data.application.provider_adapters import (
    ProviderDataRequest,
    ProviderDownload,
)
from atlas_quant.services.market_data.domain.datasets import DatasetKind
from atlas_quant.services.market_data.domain.enums import (
    AssetClass,
    Timeframe,
)
from atlas_quant.services.market_data.domain.instruments import Instrument
from atlas_quant.services.market_data.domain.provider_instrument_mappings import (
    ProviderInstrumentMapping,
)
from atlas_quant.services.market_data.domain.providers import (
    MarketDataProvider,
)


def make_request(**overrides) -> ProviderDataRequest:
    instrument = Instrument(
        symbol="EUR/USD",
        asset_class=AssetClass.FOREX,
    )

    provider = MarketDataProvider(
        provider_id="dukascopy",
        name="Dukascopy",
        supported_asset_classes=(AssetClass.FOREX,),
        supported_timeframes=(Timeframe.M1, Timeframe.M5),
    )

    mapping = ProviderInstrumentMapping(
        instrument_id=instrument.instrument_id,
        provider_id=provider.provider_id,
        provider_symbol="EURUSD",
    )

    values = {
        "provider": provider,
        "instrument": instrument,
        "mapping": mapping,
        "data_kind": DatasetKind.OHLCV,
        "timeframe": Timeframe.M1,
        "start": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "end": datetime(2026, 1, 2, tzinfo=timezone.utc),
    }
    values.update(overrides)

    return ProviderDataRequest(**values)


def test_create_valid_ohlcv_request():
    request = make_request()

    assert request.provider.provider_id == "dukascopy"
    assert request.instrument.symbol == "EUR/USD"
    assert request.mapping.provider_symbol == "EURUSD"
    assert request.data_kind == DatasetKind.OHLCV
    assert request.timeframe == Timeframe.M1


def test_request_normalizes_timestamps_to_utc():
    offset = timezone(timedelta(hours=3))

    request = make_request(
        start=datetime(2026, 1, 1, 3, tzinfo=offset),
        end=datetime(2026, 1, 2, 3, tzinfo=offset),
    )

    assert request.start.hour == 0
    assert request.end.hour == 0
    assert request.start.utcoffset() == timedelta(0)


def test_request_rejects_naive_timestamp():
    with pytest.raises(ValueError, match="timezone information"):
        make_request(start=datetime(2026, 1, 1))


def test_request_rejects_reversed_date_range():
    with pytest.raises(ValueError, match="before"):
        make_request(
            start=datetime(2026, 1, 2, tzinfo=timezone.utc),
            end=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )


def test_request_rejects_disabled_provider():
    provider = MarketDataProvider(
        provider_id="dukascopy",
        name="Dukascopy",
        supported_asset_classes=(AssetClass.FOREX,),
        supported_timeframes=(Timeframe.M1,),
        is_enabled=False,
    )

    instrument = Instrument(
        symbol="EUR/USD",
        asset_class=AssetClass.FOREX,
    )

    mapping = ProviderInstrumentMapping(
        instrument_id=instrument.instrument_id,
        provider_id="dukascopy",
        provider_symbol="EURUSD",
    )

    with pytest.raises(ValueError, match="disabled"):
        make_request(
            provider=provider,
            instrument=instrument,
            mapping=mapping,
        )


def test_request_rejects_mapping_for_another_instrument():
    request = make_request()
    mapping = ProviderInstrumentMapping(
        instrument_id=uuid4(),
        provider_id=request.provider.provider_id,
        provider_symbol="EURUSD",
    )

    with pytest.raises(ValueError, match="different instrument"):
        make_request(mapping=mapping)


def test_request_rejects_mapping_for_another_provider():
    request = make_request()
    mapping = ProviderInstrumentMapping(
        instrument_id=request.instrument.instrument_id,
        provider_id="another-provider",
        provider_symbol="EURUSD",
    )

    with pytest.raises(ValueError, match="different provider"):
        make_request(mapping=mapping)


def test_request_rejects_inactive_mapping():
    request = make_request()
    mapping = ProviderInstrumentMapping(
        instrument_id=request.instrument.instrument_id,
        provider_id=request.provider.provider_id,
        provider_symbol="EURUSD",
        is_active=False,
    )

    with pytest.raises(ValueError, match="inactive"):
        make_request(mapping=mapping)


def test_request_rejects_unsupported_timeframe():
    with pytest.raises(ValueError, match="timeframe"):
        make_request(timeframe=Timeframe.H1)


def test_request_rejects_unsupported_asset_class():
    instrument = Instrument(
        symbol="AAPL",
        asset_class=AssetClass.EQUITY,
    )

    mapping = ProviderInstrumentMapping(
        instrument_id=instrument.instrument_id,
        provider_id="dukascopy",
        provider_symbol="AAPL",
    )

    with pytest.raises(ValueError, match="asset class"):
        make_request(
            instrument=instrument,
            mapping=mapping,
        )


def test_create_valid_quote_request():
    provider = MarketDataProvider(
        provider_id="quote-provider",
        name="Quote Provider",
        supported_asset_classes=(AssetClass.FOREX,),
        supported_timeframes=(),
        supports_historical_data=False,
        supports_quote_data=True,
    )

    instrument = Instrument(
        symbol="EUR/USD",
        asset_class=AssetClass.FOREX,
    )

    mapping = ProviderInstrumentMapping(
        instrument_id=instrument.instrument_id,
        provider_id=provider.provider_id,
        provider_symbol="EURUSD",
    )

    request = make_request(
        provider=provider,
        instrument=instrument,
        mapping=mapping,
        data_kind=DatasetKind.BID_ASK,
        timeframe=None,
    )

    assert request.data_kind == DatasetKind.BID_ASK
    assert request.timeframe is None


def test_quote_request_rejects_timeframe():
    provider = MarketDataProvider(
        provider_id="quote-provider",
        name="Quote Provider",
        supported_asset_classes=(AssetClass.FOREX,),
        supported_timeframes=(),
        supports_historical_data=False,
        supports_quote_data=True,
    )

    instrument = Instrument(
        symbol="EUR/USD",
        asset_class=AssetClass.FOREX,
    )

    mapping = ProviderInstrumentMapping(
        instrument_id=instrument.instrument_id,
        provider_id=provider.provider_id,
        provider_symbol="EURUSD",
    )

    with pytest.raises(ValueError, match="must not specify"):
        make_request(
            provider=provider,
            instrument=instrument,
            mapping=mapping,
            data_kind=DatasetKind.BID_ASK,
            timeframe=Timeframe.M1,
        )


def test_download_accepts_original_binary_payload():
    payload = BytesIO(b"original,provider,payload")

    download = ProviderDownload(
        payload=payload,
        content_type=" text/csv ",
        provider_schema_version=" v1 ",
    )

    assert download.content_type == "text/csv"
    assert download.provider_schema_version == "v1"
    assert payload.tell() == 0
    assert payload.read() == b"original,provider,payload"

def test_download_rejects_text_stream():
    with pytest.raises(TypeError, match="binary stream"):
        ProviderDownload(payload=StringIO("text payload"))

def test_download_normalizes_download_timestamp():
    offset = timezone(timedelta(hours=3))

    download = ProviderDownload(
        payload=BytesIO(b"data"),
        downloaded_at=datetime(2026, 1, 1, 3, tzinfo=offset),
    )

    assert download.downloaded_at.hour == 0
    assert download.downloaded_at.utcoffset() == timedelta(0)


def test_download_rejects_empty_content_type():
    with pytest.raises(ValueError, match="content_type"):
        ProviderDownload(
            payload=BytesIO(b"data"),
            content_type="  ",
        )


def test_download_rejects_naive_timestamp():
    with pytest.raises(ValueError, match="timezone information"):
        ProviderDownload(
            payload=BytesIO(b"data"),
            downloaded_at=datetime(2026, 1, 1),
        )


def test_download_context_manager_closes_payload():
    payload = BytesIO(b"data")

    with ProviderDownload(payload=payload) as download:
        assert download.payload.read() == b"data"

    assert payload.closed