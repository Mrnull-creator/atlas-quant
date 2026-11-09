from __future__ import annotations

import asyncio
import lzma
import struct
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from io import BytesIO

import pytest

from atlas_quant.services.market_data.application.provider_adapters import (
    ProviderDataRequest,
    ProviderDownload,
)
from atlas_quant.services.market_data.domain.datasets import DatasetKind
from atlas_quant.services.market_data.domain.enums import AssetClass
from atlas_quant.services.market_data.domain.instruments import Instrument
from atlas_quant.services.market_data.domain.provider_instrument_mappings import (
    ProviderInstrumentMapping,
)
from atlas_quant.services.market_data.domain.providers import (
    MarketDataProvider,
)
from atlas_quant.services.market_data.infrastructure.dukascopy_adapter import (
    DUKASCOPY_BI5_SCHEMA_VERSION,
    DukascopyNoDataError,
    DukascopyProviderAdapter,
)
from atlas_quant.services.market_data.infrastructure.dukascopy_s3 import (
    DukascopyDailyDownload,
)


RECORD = struct.Struct(">IIIff")

TRADING_DATE = date(2025, 1, 15)
PERIOD_START = datetime.combine(
    TRADING_DATE,
    time.min,
    tzinfo=timezone.utc,
)


def make_payload(
    records: list[tuple[int, int, int, float, float]],
) -> bytes:
    raw = b"".join(RECORD.pack(*record) for record in records)
    return lzma.compress(raw, format=lzma.FORMAT_ALONE)


class FakeDownloader:
    def __init__(self, result=None):
        self.result = result
        self.calls = []

    async def download_daily(self, symbol, trading_date):
        self.calls.append((symbol, trading_date))
        return self.result


def make_request(
    *,
    start: datetime = PERIOD_START,
    end: datetime = PERIOD_START + timedelta(days=1),
) -> ProviderDataRequest:
    instrument = Instrument(
        symbol="EUR/USD",
        asset_class=AssetClass.FOREX,
    )

    provider = MarketDataProvider(
        provider_id="dukascopy",
        name="Dukascopy",
        supported_asset_classes=(AssetClass.FOREX,),
        supported_timeframes=(),
        supports_historical_data=False,
        supports_quote_data=True,
    )

    mapping = ProviderInstrumentMapping(
        instrument_id=instrument.instrument_id,
        provider_id="dukascopy",
        provider_symbol="EURUSD",
    )

    return ProviderDataRequest(
        provider=provider,
        instrument=instrument,
        mapping=mapping,
        data_kind=DatasetKind.BID_ASK,
        timeframe=None,
        start=start,
        end=end,
    )


def make_download(
    request: ProviderDataRequest,
    payload: bytes,
) -> DukascopyDailyDownload:
    return DukascopyDailyDownload(
        symbol=request.mapping.provider_symbol,
        trading_date=TRADING_DATE,
        bucket="test-bucket",
        object_key="EURUSD/2025/00/15_ticks.bi5",
        payload=payload,
        downloaded_at=datetime(2025, 1, 16, tzinfo=timezone.utc),
    )


def make_adapter(
    request: ProviderDataRequest,
    downloader: FakeDownloader,
) -> DukascopyProviderAdapter:
    return DukascopyProviderAdapter(
        downloader,
        price_divisors={
            request.instrument.instrument_id: 100_000,
        },
    )


def test_download_preserves_original_bi5_payload():
    request = make_request()
    payload = make_payload(
        [(0, 108456, 108455, 1.25, 2.50)]
    )
    downloader = FakeDownloader(make_download(request, payload))
    adapter = make_adapter(request, downloader)

    result = asyncio.run(adapter.download(request))

    assert result.payload.read() == payload
    assert result.payload.tell() == len(payload)
    assert result.content_type == "application/octet-stream"
    assert result.provider_schema_version == DUKASCOPY_BI5_SCHEMA_VERSION

    assert downloader.calls == [("EURUSD", TRADING_DATE)]


def test_normalize_uses_canonical_symbol_and_configured_price_divisor():
    request = make_request()
    payload = make_payload(
        [(0, 108456, 108455, 1.25, 2.50)]
    )
    adapter = make_adapter(request, FakeDownloader())

    download = ProviderDownload(payload=BytesIO(payload))

    result = tuple(adapter.normalize(download, request))

    assert len(result) == 1
    assert result[0].symbol == "EUR/USD"
    assert result[0].bid == Decimal("1.08455")
    assert result[0].ask == Decimal("1.08456")


def test_normalize_filters_to_requested_half_open_interval():
    start = PERIOD_START + timedelta(hours=12)
    end = PERIOD_START + timedelta(hours=13)
    request = make_request(start=start, end=end)

    payload = make_payload(
        [
            (11 * 60 * 60 * 1000, 108456, 108455, 1.0, 1.0),
            (12 * 60 * 60 * 1000, 108457, 108456, 1.0, 1.0),
            (13 * 60 * 60 * 1000, 108458, 108457, 1.0, 1.0),
        ]
    )

    adapter = make_adapter(request, FakeDownloader())
    download = ProviderDownload(
        payload=BytesIO(payload)
    )

    result = tuple(adapter.normalize(download, request))

    assert len(result) == 1
    assert result[0].timestamp == start


def test_normalize_restores_payload_stream_to_start():
    request = make_request()
    payload = make_payload(
        [(0, 108456, 108455, 1.0, 1.0)]
    )

    adapter = make_adapter(request, FakeDownloader())
    download = ProviderDownload(
        payload=BytesIO(payload)
    )

    tuple(adapter.normalize(download, request))

    assert download.payload.tell() == 0
    assert download.payload.read() == payload


def test_download_raises_specific_error_when_daily_file_is_missing():
    request = make_request()
    downloader = FakeDownloader(None)
    adapter = make_adapter(request, downloader)

    with pytest.raises(DukascopyNoDataError):
        asyncio.run(adapter.download(request))


def test_adapter_rejects_multi_day_request():
    request = make_request(
        start=PERIOD_START,
        end=PERIOD_START + timedelta(days=2),
    )

    adapter = make_adapter(request, FakeDownloader())

    with pytest.raises(ValueError, match="one UTC calendar day"):
        asyncio.run(adapter.download(request))


def test_adapter_rejects_missing_price_divisor():
    request = make_request()

    adapter = DukascopyProviderAdapter(
        FakeDownloader(),
        price_divisors={},
    )

    with pytest.raises(ValueError, match="No Dukascopy price divisor"):
        asyncio.run(adapter.download(request))


@pytest.mark.parametrize("divisor", [0, -1, True, 1.5])
def test_adapter_rejects_invalid_price_divisor(divisor):
    request = make_request()

    with pytest.raises(ValueError, match="positive integer"):
        DukascopyProviderAdapter(
            FakeDownloader(),
            price_divisors={
                request.instrument.instrument_id: divisor,
            },
        )


def test_adapter_rejects_non_uuid_price_divisor_key():
    with pytest.raises(TypeError, match="instrument UUID"):
        DukascopyProviderAdapter(
            FakeDownloader(),
            price_divisors={"EUR/USD": 100_000},
        )