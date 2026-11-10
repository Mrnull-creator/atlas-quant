import asyncio
from functools import wraps
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from atlas_quant.services.market_data.application.dataset_publication import (
    DatasetPublicationDatasetKindError,
    DatasetPublicationRejectedError,
    DatasetPublicationService,
    DatasetPublicationStorageError,
)
from atlas_quant.services.market_data.application.storage import (
    StorageObjectMetadata,
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
from atlas_quant.services.market_data.domain.quality import (
    FindingSeverity,
)
from atlas_quant.services.market_data.domain.quotes import BidAskQuote


BASE_TIMESTAMP = datetime(2026, 1, 1, tzinfo=timezone.utc)


def async_test(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))

    return wrapper


def make_bar(
    *,
    minute=0,
    symbol="EURUSD",
    timeframe=Timeframe.M1,
):
    return OHLCVBar(
        symbol=symbol,
        timeframe=timeframe,
        timestamp=BASE_TIMESTAMP + timedelta(minutes=minute),
        open=Decimal("1.1000"),
        high=Decimal("1.1010"),
        low=Decimal("1.0990"),
        close=Decimal("1.1005"),
        volume=Decimal("100"),
    )


def make_quote(*, minute=0, symbol="EURUSD"):
    return BidAskQuote(
        symbol=symbol,
        timestamp=BASE_TIMESTAMP + timedelta(minutes=minute),
        bid=Decimal("1.1000"),
        ask=Decimal("1.1002"),
        bid_size=Decimal("100"),
        ask_size=Decimal("150"),
    )


def make_dataset(kind=DatasetKind.OHLCV):
    timeframe = Timeframe.M1 if kind == DatasetKind.OHLCV else None

    return Dataset(
        name="EURUSD historical data",
        instrument_id=uuid4(),
        data_kind=kind,
        asset_class=AssetClass.FOREX,
        instrument_symbol="EURUSD",
        timeframe=timeframe,
    )


def make_version(dataset, *, storage_format="parquet"):
    return DatasetVersion(
        dataset_id=dataset.dataset_id,
        version_number=1,
        schema_version="1",
        storage_format=storage_format,
    )


def make_metadata(
    observations,
    *,
    storage_format="parquet",
    record_count=None,
    first_timestamp=None,
    last_timestamp=None,
):
    timestamps = [item.timestamp for item in observations]

    return StorageObjectMetadata(
        storage_uri="market-data/test/dataset.parquet",
        storage_format=storage_format,
        checksum_algorithm="sha256",
        checksum="a" * 64,
        size_bytes=1024,
        record_count=(
            len(observations)
            if record_count is None
            else record_count
        ),
        first_timestamp=(
            min(timestamps)
            if first_timestamp is None and timestamps
            else first_timestamp
        ),
        last_timestamp=(
            max(timestamps)
            if last_timestamp is None and timestamps
            else last_timestamp
        ),
    )


class FakeDatasetRepository:
    def __init__(self, dataset):
        self.dataset = dataset
        self.get_calls = []

    async def get_by_id(self, dataset_id):
        self.get_calls.append(dataset_id)

        if self.dataset.dataset_id == dataset_id:
            return self.dataset

        return None


class FakeLifecycleRepository:
    def __init__(self, dataset, version=None):
        self.dataset = dataset
        self.version = version or make_version(dataset)

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

        self.version = version
        self.created_versions.append(version)

        return version

    async def publish_staging_version(
        self,
        dataset_version_id,
        metadata,
    ):
        assert dataset_version_id == self.version.dataset_version_id

        self.published_versions.append(
            (dataset_version_id, metadata)
        )

        return DatasetVersion(
            dataset_id=self.version.dataset_id,
            dataset_version_id=self.version.dataset_version_id,
            version_number=self.version.version_number,
            schema_version=self.version.schema_version,
            storage_format=self.version.storage_format,
            status=DatasetVersionStatus.AVAILABLE,
            first_timestamp=metadata.first_timestamp,
            last_timestamp=metadata.last_timestamp,
            record_count=metadata.record_count,
            storage_uri=metadata.storage_uri,
            checksum_algorithm=metadata.checksum_algorithm,
            checksum=metadata.checksum,
            created_at=self.version.created_at,
            published_at=datetime.now(timezone.utc),
        )

    async def fail_staging_version(self, dataset_version_id):
        self.failed_versions.append(dataset_version_id)
        return self.version


class FakeStorage:
    def __init__(
        self,
        *,
        metadata_factory=None,
        verify_error=None,
        write_error=None,
    ):
        self.metadata_factory = metadata_factory
        self.verify_error = verify_error
        self.write_error = write_error

        self.write_calls = []
        self.verify_calls = []

    async def write(self, dataset_version_id, observations):
        self.write_calls.append(
            (dataset_version_id, tuple(observations))
        )

        if self.write_error is not None:
            raise self.write_error

        if self.metadata_factory is not None:
            return self.metadata_factory(observations)

        return make_metadata(observations)

    async def verify(self, dataset_version_id, expected):
        self.verify_calls.append((dataset_version_id, expected))

        if self.verify_error is not None:
            raise self.verify_error

    def read(self, dataset_version_id, start=None, end=None):
        raise NotImplementedError


def make_service(
    dataset,
    *,
    version=None,
    ohlcv_storage=None,
    quote_storage=None,
):
    datasets = FakeDatasetRepository(dataset)
    lifecycle = FakeLifecycleRepository(dataset, version)

    ohlcv = ohlcv_storage or FakeStorage()
    quotes = quote_storage or FakeStorage()

    service = DatasetPublicationService(
        datasets,
        lifecycle,
        ohlcv,
        quotes,
    )

    return service, datasets, lifecycle, ohlcv, quotes


@async_test
async def test_publish_ohlcv_writes_verifies_and_publishes():
    dataset = make_dataset()
    service, _, lifecycle, storage, _ = make_service(dataset)

    bars = [
        make_bar(minute=0),
        make_bar(minute=1),
        make_bar(minute=2),
    ]

    result = await service.publish_ohlcv(
        dataset.dataset_id,
        bars,
    )

    assert result.dataset_version.status == DatasetVersionStatus.AVAILABLE
    assert result.dataset_version.record_count == 3
    assert result.quality_report.checked_records == 3
    assert result.quality_report.is_valid

    assert len(storage.write_calls) == 1
    assert len(storage.verify_calls) == 1
    assert len(lifecycle.published_versions) == 1
    assert lifecycle.failed_versions == []


@async_test
async def test_publish_ohlcv_materializes_generator_once():
    dataset = make_dataset()
    service, _, _, storage, _ = make_service(dataset)

    consumed = []

    def observations():
        for minute in range(3):
            consumed.append(minute)
            yield make_bar(minute=minute)

    result = await service.publish_ohlcv(
        dataset.dataset_id,
        observations(),
    )

    assert result.quality_report.checked_records == 3
    assert consumed == [0, 1, 2]
    assert len(storage.write_calls) == 1


@async_test
async def test_publish_ohlcv_preserves_quality_warnings():
    dataset = make_dataset()
    service, _, _, _, _ = make_service(dataset)

    bars = [
        make_bar(minute=0),
        make_bar(minute=2),
        make_bar(minute=1),
    ]

    result = await service.publish_ohlcv(
        dataset.dataset_id,
        bars,
    )

    assert result.dataset_version.status == DatasetVersionStatus.AVAILABLE
    assert result.quality_report.is_valid
    assert result.quality_report.has_warnings
    assert result.quality_report.warning_count == 1


@pytest.mark.parametrize(
    ("bar", "expected_code"),
    [
        (
            make_bar(symbol="GBPUSD"),
            "DATASET_SYMBOL_MISMATCH",
        ),
        (
            make_bar(timeframe=Timeframe.M5),
            "DATASET_TIMEFRAME_MISMATCH",
        ),
    ],
)
@async_test
async def test_ohlcv_identity_mismatch_rejects_before_staging(
    bar,
    expected_code,
):
    dataset = make_dataset()
    service, _, lifecycle, storage, _ = make_service(dataset)

    with pytest.raises(DatasetPublicationRejectedError) as captured:
        await service.publish_ohlcv(
            dataset.dataset_id,
            [bar],
        )

    assert expected_code in [
        finding.code
        for finding in captured.value.report.findings
    ]

    assert lifecycle.created_versions == []
    assert storage.write_calls == []


@async_test
async def test_ohlcv_empty_batch_is_rejected_before_staging():
    dataset = make_dataset()
    service, _, lifecycle, storage, _ = make_service(dataset)

    with pytest.raises(DatasetPublicationRejectedError) as captured:
        await service.publish_ohlcv(dataset.dataset_id, [])

    assert captured.value.report.error_count == 1
    assert captured.value.report.findings[0].code == "EMPTY_DATASET"
    assert lifecycle.created_versions == []
    assert storage.write_calls == []


@async_test
async def test_publish_quotes_writes_verifies_and_publishes():
    dataset = make_dataset(DatasetKind.BID_ASK)
    service, _, lifecycle, _, storage = make_service(dataset)

    quotes = [
        make_quote(minute=0),
        make_quote(minute=1),
    ]

    result = await service.publish_quotes(
        dataset.dataset_id,
        quotes,
    )

    assert result.dataset_version.status == DatasetVersionStatus.AVAILABLE
    assert result.dataset_version.record_count == 2
    assert result.quality_report.is_valid

    assert len(storage.write_calls) == 1
    assert len(storage.verify_calls) == 1
    assert len(lifecycle.published_versions) == 1
    assert lifecycle.failed_versions == []


@async_test
async def test_quote_symbol_mismatch_rejects_before_staging():
    dataset = make_dataset(DatasetKind.BID_ASK)
    service, _, lifecycle, _, storage = make_service(dataset)

    with pytest.raises(DatasetPublicationRejectedError) as captured:
        await service.publish_quotes(
            dataset.dataset_id,
            [make_quote(symbol="GBPUSD")],
        )

    assert captured.value.report.findings[0].code == (
        "DATASET_SYMBOL_MISMATCH"
    )
    assert lifecycle.created_versions == []
    assert storage.write_calls == []


@async_test
async def test_wrong_dataset_kind_is_rejected():
    dataset = make_dataset(DatasetKind.BID_ASK)
    service, _, lifecycle, storage, _ = make_service(dataset)

    with pytest.raises(DatasetPublicationDatasetKindError):
        await service.publish_ohlcv(
            dataset.dataset_id,
            [make_bar()],
        )

    assert lifecycle.created_versions == []
    assert storage.write_calls == []


@pytest.mark.parametrize(
    "metadata_factory",
    [
        lambda observations: make_metadata(
            observations,
            storage_format="csv",
        ),
        lambda observations: make_metadata(
            observations,
            record_count=99,
        ),
        lambda observations: make_metadata(
            observations,
            first_timestamp=BASE_TIMESTAMP - timedelta(minutes=1),
        ),
    ],
)
@async_test
async def test_invalid_storage_metadata_fails_staging_version(
    metadata_factory,
):
    dataset = make_dataset()
    storage = FakeStorage(metadata_factory=metadata_factory)

    service, _, lifecycle, _, _ = make_service(
        dataset,
        ohlcv_storage=storage,
    )

    with pytest.raises(DatasetPublicationStorageError):
        await service.publish_ohlcv(
            dataset.dataset_id,
            [make_bar()],
        )

    assert len(lifecycle.created_versions) == 1
    assert len(lifecycle.failed_versions) == 1
    assert storage.verify_calls == []
    assert lifecycle.published_versions == []


@async_test
async def test_verification_failure_marks_staging_version_failed():
    dataset = make_dataset()
    storage = FakeStorage(
        verify_error=RuntimeError("checksum verification failed")
    )

    service, _, lifecycle, _, _ = make_service(
        dataset,
        ohlcv_storage=storage,
    )

    with pytest.raises(
        RuntimeError,
        match="checksum verification failed",
    ):
        await service.publish_ohlcv(
            dataset.dataset_id,
            [make_bar()],
        )

    assert len(storage.write_calls) == 1
    assert len(storage.verify_calls) == 1
    assert len(lifecycle.failed_versions) == 1
    assert lifecycle.published_versions == []


@async_test
async def test_storage_write_failure_marks_staging_version_failed():
    dataset = make_dataset()
    storage = FakeStorage(
        write_error=RuntimeError("disk write failed")
    )

    service, _, lifecycle, _, _ = make_service(
        dataset,
        ohlcv_storage=storage,
    )

    with pytest.raises(RuntimeError, match="disk write failed"):
        await service.publish_ohlcv(
            dataset.dataset_id,
            [make_bar()],
        )

    assert len(lifecycle.failed_versions) == 1
    assert lifecycle.published_versions == []


@async_test
async def test_cancellation_leaves_staging_version_for_recovery():
    dataset = make_dataset()

    class CancellingStorage(FakeStorage):
        async def write(self, dataset_version_id, observations):
            self.write_calls.append(
                (dataset_version_id, tuple(observations))
            )
            raise asyncio.CancelledError()

    storage = CancellingStorage()

    service, _, lifecycle, _, _ = make_service(
        dataset,
        ohlcv_storage=storage,
    )

    with pytest.raises(asyncio.CancelledError):
        await service.publish_ohlcv(
            dataset.dataset_id,
            [make_bar()],
        )

    assert len(lifecycle.created_versions) == 1
    assert lifecycle.failed_versions == []
    assert lifecycle.published_versions == []


@async_test
async def test_missing_dataset_is_rejected():
    dataset = make_dataset()
    service, _, lifecycle, storage, _ = make_service(dataset)

    with pytest.raises(Exception) as captured:
        await service.publish_ohlcv(
            uuid4(),
            [make_bar()],
        )

    assert "does not exist" in str(captured.value)
    assert lifecycle.created_versions == []
    assert storage.write_calls == []
