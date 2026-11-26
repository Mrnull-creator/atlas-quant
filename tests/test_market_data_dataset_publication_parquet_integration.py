import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from atlas_quant.services.market_data.application.dataset_publication import (
    DatasetPublicationRejectedError,
    DatasetPublicationService,
)
from atlas_quant.services.market_data.domain.bars import OHLCVBar
from atlas_quant.services.market_data.domain.datasets import (
    Dataset,
    DatasetKind,
    DatasetVersion,
    DatasetVersionStatus,
)
from atlas_quant.services.market_data.domain.enums import (
    AssetClass,
    Timeframe,
)
from atlas_quant.services.market_data.domain.quotes import BidAskQuote
from atlas_quant.services.market_data.infrastructure.parquet.storage import (
    ParquetOHLCVStorage,
    ParquetQuoteStorage,
)


BASE_TIMESTAMP = datetime(2026, 1, 1, tzinfo=timezone.utc)


def async_test(function):
    def wrapper(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))

    return wrapper


def make_dataset(kind=DatasetKind.OHLCV):
    return Dataset(
        name="EURUSD historical data",
        instrument_id=uuid4(),
        data_kind=kind,
        asset_class=AssetClass.FOREX,
        instrument_symbol="EURUSD",
        timeframe=Timeframe.M1 if kind == DatasetKind.OHLCV else None,
    )


def make_bar(minute=0):
    return OHLCVBar(
        symbol="EURUSD",
        timeframe=Timeframe.M1,
        timestamp=BASE_TIMESTAMP + timedelta(minutes=minute),
        open=Decimal("1.1000"),
        high=Decimal("1.1010"),
        low=Decimal("1.0990"),
        close=Decimal("1.1005"),
        volume=Decimal("100"),
    )


def make_quote(minute=0):
    return BidAskQuote(
        symbol="EURUSD",
        timestamp=BASE_TIMESTAMP + timedelta(minutes=minute),
        bid=Decimal("1.1000"),
        ask=Decimal("1.1002"),
        bid_size=Decimal("100"),
        ask_size=Decimal("150"),
    )


class FakeDatasetRepository:
    def __init__(self, dataset):
        self.dataset = dataset

    async def get_by_id(self, dataset_id):
        if dataset_id == self.dataset.dataset_id:
            return self.dataset
        return None


class FakeLifecycleRepository:
    def __init__(self, dataset):
        self.dataset = dataset
        self.created_versions = []
        self.published_versions = []
        self.failed_versions = []

    async def create_next_staging_version(
        self,
        dataset_id,
        *,
        schema_version,
        storage_format,
    ):
        assert dataset_id == self.dataset.dataset_id

        version = DatasetVersion(
            dataset_id=dataset_id,
            version_number=1,
            schema_version=schema_version,
            storage_format=storage_format,
        )
        self.created_versions.append(version)
        return version

    async def publish_staging_version(
        self,
        dataset_version_id,
        metadata,
    ):
        version = self.created_versions[-1]
        assert dataset_version_id == version.dataset_version_id

        self.published_versions.append(
            (dataset_version_id, metadata)
        )

        return DatasetVersion(
            dataset_id=version.dataset_id,
            dataset_version_id=version.dataset_version_id,
            version_number=version.version_number,
            schema_version=version.schema_version,
            storage_format=version.storage_format,
            status=DatasetVersionStatus.AVAILABLE,
            first_timestamp=metadata.first_timestamp,
            last_timestamp=metadata.last_timestamp,
            record_count=metadata.record_count,
            storage_uri=metadata.storage_uri,
            checksum_algorithm=metadata.checksum_algorithm,
            checksum=metadata.checksum,
            created_at=version.created_at,
            published_at=datetime.now(timezone.utc),
        )

    async def fail_staging_version(self, dataset_version_id):
        self.failed_versions.append(dataset_version_id)


def make_service(dataset, storage):
    datasets = FakeDatasetRepository(dataset)
    lifecycle = FakeLifecycleRepository(dataset)

    service = DatasetPublicationService(
        datasets,
        lifecycle,
        storage if dataset.data_kind == DatasetKind.OHLCV else None,
        storage if dataset.data_kind == DatasetKind.BID_ASK else None,
    )

    return service, lifecycle


async def collect(iterator):
    return [item async for item in iterator]


@pytest.mark.parametrize("record_count", [3, 2500])
def test_ohlcv_publication_with_real_parquet_storage(
    tmp_path,
    record_count,
):
    async def run():
        dataset = make_dataset()
        storage = ParquetOHLCVStorage(tmp_path)
        service, lifecycle = make_service(dataset, storage)

        bars = (
            make_bar(minute=index)
            for index in range(record_count)
        )

        result = await service.publish_ohlcv(
            dataset.dataset_id,
            bars,
        )

        assert result.dataset_version.status == DatasetVersionStatus.AVAILABLE
        assert result.dataset_version.record_count == record_count
        assert result.quality_report.checked_records == record_count
        assert result.quality_report.is_valid

        assert len(lifecycle.created_versions) == 1
        assert len(lifecycle.published_versions) == 1
        assert lifecycle.failed_versions == []

        version_id = result.dataset_version.dataset_version_id

        restored = await collect(
            storage.read(
                version_id,
                BASE_TIMESTAMP,
                BASE_TIMESTAMP + timedelta(minutes=record_count),
            )
        )

        assert len(restored) == record_count
        assert restored[0] == make_bar(minute=0)
        assert restored[-1] == make_bar(minute=record_count - 1)

        metadata = lifecycle.published_versions[0][1]
        assert metadata.record_count == record_count
        assert metadata.first_timestamp == BASE_TIMESTAMP
        assert metadata.last_timestamp == (
            BASE_TIMESTAMP + timedelta(minutes=record_count - 1)
        )
        assert metadata.size_bytes > 0
        assert metadata.checksum_algorithm == "sha256"

    asyncio.run(run())


def test_quote_publication_with_real_parquet_storage(tmp_path):
    async def run():
        dataset = make_dataset(DatasetKind.BID_ASK)
        storage = ParquetQuoteStorage(tmp_path)
        service, lifecycle = make_service(dataset, storage)

        quotes = [
            make_quote(minute=0),
            make_quote(minute=1),
            make_quote(minute=2),
        ]

        result = await service.publish_quotes(
            dataset.dataset_id,
            iter(quotes),
        )

        assert result.dataset_version.status == DatasetVersionStatus.AVAILABLE
        assert result.dataset_version.record_count == len(quotes)
        assert result.quality_report.checked_records == len(quotes)
        assert result.quality_report.is_valid

        assert len(lifecycle.published_versions) == 1
        assert lifecycle.failed_versions == []

        version_id = result.dataset_version.dataset_version_id

        restored = await collect(
            storage.read(
                version_id,
                BASE_TIMESTAMP,
                BASE_TIMESTAMP + timedelta(minutes=3),
            )
        )

        assert restored == quotes

        metadata = lifecycle.published_versions[0][1]
        assert metadata.record_count == len(quotes)
        assert metadata.first_timestamp == quotes[0].timestamp
        assert metadata.last_timestamp == quotes[-1].timestamp
        assert metadata.size_bytes > 0
        assert metadata.checksum_algorithm == "sha256"

    asyncio.run(run())


def test_invalid_ohlcv_stream_is_not_published(tmp_path):
    async def run():
        dataset = make_dataset()
        storage = ParquetOHLCVStorage(tmp_path)
        service, lifecycle = make_service(dataset, storage)

        bars = [
            make_bar(minute=0),
            make_bar(minute=0),
        ]

        with pytest.raises(DatasetPublicationRejectedError):
            await service.publish_ohlcv(
                dataset.dataset_id,
                iter(bars),
            )

        assert len(lifecycle.created_versions) == 1
        assert lifecycle.published_versions == []
        assert len(lifecycle.failed_versions) == 1

    asyncio.run(run())


def test_invalid_quote_stream_is_not_published(tmp_path):
    async def run():
        dataset = make_dataset(DatasetKind.BID_ASK)
        storage = ParquetQuoteStorage(tmp_path)
        service, lifecycle = make_service(dataset, storage)

        quotes = [
            make_quote(minute=0),
            make_quote(minute=0),
        ]

        with pytest.raises(DatasetPublicationRejectedError):
            await service.publish_quotes(
                dataset.dataset_id,
                iter(quotes),
            )

        assert len(lifecycle.created_versions) == 1
        assert lifecycle.published_versions == []
        assert len(lifecycle.failed_versions) == 1

    asyncio.run(run())