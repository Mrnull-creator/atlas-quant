
"""Tests for market data dataset domain models."""

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

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


def make_dataset(**overrides) -> Dataset:
    values = {
        "name": "EUR/USD One Minute",
        "data_kind": DatasetKind.OHLCV,
        "asset_class": AssetClass.FOREX,
        "instrument_id": uuid4(),
        "instrument_symbol": "EUR/USD",
        "provider_id": "dukascopy",
        "timeframe": Timeframe.M1,
    }
    values.update(overrides)
    return Dataset(**values)


def make_version(**overrides) -> DatasetVersion:
    created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)

    values = {
        "dataset_id": uuid4(),
        "version_number": 1,
        "schema_version": "1.0",
        "storage_format": "parquet",
        "record_count": 2,
        "first_timestamp": datetime(
            2026, 1, 1, 9, 0, tzinfo=timezone.utc
        ),
        "last_timestamp": datetime(
            2026, 1, 1, 9, 1, tzinfo=timezone.utc
        ),
        "created_at": created_at,
    }
    values.update(overrides)
    return DatasetVersion(**values)


# Dataset identity tests


def test_create_dataset():
    dataset = make_dataset()

    assert dataset.name == "EUR/USD One Minute"
    assert dataset.data_kind == DatasetKind.OHLCV
    assert dataset.asset_class == AssetClass.FOREX
    assert dataset.instrument_symbol == "EUR/USD"
    assert dataset.provider_id == "dukascopy"
    assert dataset.timeframe == Timeframe.M1
    assert dataset.latest_version_id is None


def test_dataset_generates_uuid():
    dataset = make_dataset()

    assert dataset.dataset_id is not None


def test_dataset_normalizes_name():
    dataset = make_dataset(name="  EUR/USD Historical Data  ")

    assert dataset.name == "EUR/USD Historical Data"


def test_dataset_normalizes_instrument_symbol():
    dataset = make_dataset(instrument_symbol="  eur/usd  ")

    assert dataset.instrument_symbol == "EUR/USD"


def test_dataset_normalizes_provider_id():
    dataset = make_dataset(provider_id="  Dukascopy  ")

    assert dataset.provider_id == "dukascopy"


def test_dataset_supports_raw_provider_data_without_instrument():
    dataset = make_dataset(
        data_kind=DatasetKind.RAW_PROVIDER,
        instrument_id=None,
        instrument_symbol=None,
        timeframe=None,
    )

    assert dataset.data_kind == DatasetKind.RAW_PROVIDER
    assert dataset.instrument_id is None
    assert dataset.instrument_symbol is None
    assert dataset.timeframe is None


def test_dataset_supports_raw_provider_data_with_instrument():
    dataset = make_dataset(
        data_kind=DatasetKind.RAW_PROVIDER,
    )

    assert dataset.instrument_symbol == "EUR/USD"


def test_dataset_allows_normalized_data_without_single_provider():
    dataset = make_dataset(provider_id=None)

    assert dataset.provider_id is None


def test_dataset_rejects_empty_name():
    with pytest.raises(ValueError, match="name cannot be empty"):
        make_dataset(name="  ")


def test_dataset_rejects_invalid_data_kind():
    with pytest.raises(TypeError, match="DatasetKind"):
        make_dataset(data_kind="ohlcv")


def test_dataset_rejects_invalid_asset_class():
    with pytest.raises(TypeError, match="AssetClass"):
        make_dataset(asset_class="forex")


def test_dataset_rejects_invalid_timeframe():
    with pytest.raises(TypeError, match="Timeframe"):
        make_dataset(timeframe="1m")


def test_dataset_rejects_empty_instrument_symbol():
    with pytest.raises(ValueError, match="instrument_symbol"):
        make_dataset(instrument_symbol="  ")


def test_dataset_rejects_empty_provider_id():
    with pytest.raises(ValueError, match="provider_id"):
        make_dataset(provider_id="  ")


def test_dataset_rejects_invalid_provider_id():
    with pytest.raises(ValueError, match="provider_id"):
        make_dataset(provider_id="provider name")


def test_raw_dataset_requires_provider():
    with pytest.raises(ValueError, match="require a provider_id"):
        make_dataset(
            data_kind=DatasetKind.RAW_PROVIDER,
            provider_id=None,
        )


@pytest.mark.parametrize(
    "data_kind",
    [DatasetKind.OHLCV, DatasetKind.BID_ASK],
)
def test_normalized_dataset_requires_instrument(data_kind):
    with pytest.raises(ValueError, match="require an instrument_symbol"):
        make_dataset(
            data_kind=data_kind,
            instrument_symbol=None,
        )


def test_ohlcv_dataset_requires_timeframe():
    with pytest.raises(ValueError, match="OHLCV datasets require a timeframe"):
        make_dataset(
            data_kind=DatasetKind.OHLCV,
            timeframe=None,
        )


def test_bid_ask_dataset_does_not_require_timeframe():
    dataset = make_dataset(
        data_kind=DatasetKind.BID_ASK,
        timeframe=None,
    )

    assert dataset.data_kind == DatasetKind.BID_ASK
    assert dataset.instrument_id is not None
    assert dataset.instrument_symbol == "EUR/USD"
    assert dataset.timeframe is None


def test_bid_ask_dataset_rejects_timeframe():
    with pytest.raises(ValueError, match="must not have a timeframe"):
        make_dataset(
            data_kind=DatasetKind.BID_ASK,
            timeframe=Timeframe.M1,
        )


def test_dataset_rejects_invalid_dataset_id():
    with pytest.raises(TypeError, match="dataset_id"):
        make_dataset(dataset_id="not-a-uuid")


def test_dataset_rejects_invalid_instrument_id():
    with pytest.raises(TypeError, match="instrument_id"):
        make_dataset(instrument_id="not-a-uuid")


def test_normalized_dataset_requires_instrument_id():
    with pytest.raises(ValueError, match="require an instrument_id"):
        make_dataset(instrument_id=None)


def test_dataset_rejects_invalid_latest_version_id():
    with pytest.raises(TypeError, match="latest_version_id"):
        make_dataset(latest_version_id="not-a-uuid")


def test_dataset_is_immutable():
    dataset = make_dataset()

    with pytest.raises(FrozenInstanceError):
        dataset.name = "Changed"


# Dataset version tests


def test_create_staging_dataset_version():
    version = make_version()

    assert version.version_number == 1
    assert version.status == DatasetVersionStatus.STAGING
    assert version.record_count == 2
    assert version.storage_format == "parquet"
    assert version.published_at is None


def test_dataset_version_generates_uuid():
    version = make_version()

    assert version.dataset_version_id is not None


def test_dataset_version_normalizes_storage_format():
    version = make_version(storage_format="  PARQUET  ")

    assert version.storage_format == "parquet"


def test_dataset_version_normalizes_schema_version():
    version = make_version(schema_version="  1.0  ")

    assert version.schema_version == "1.0"


def test_dataset_version_normalizes_storage_uri():
    version = make_version(storage_uri="  s3://bucket/data  ")

    assert version.storage_uri == "s3://bucket/data"


def test_dataset_version_normalizes_checksum_metadata():
    version = make_version(
        checksum_algorithm="  SHA256  ",
        checksum="  abc123  ",
    )

    assert version.checksum_algorithm == "sha256"
    assert version.checksum == "abc123"


def test_dataset_version_normalizes_timestamps_to_utc():
    offset = timezone(timedelta(hours=3))

    version = make_version(
        first_timestamp=datetime(2026, 1, 1, 12, 0, tzinfo=offset),
        last_timestamp=datetime(2026, 1, 1, 12, 1, tzinfo=offset),
    )

    assert version.first_timestamp.hour == 9
    assert version.first_timestamp.utcoffset() == timedelta(0)


def test_dataset_version_allows_empty_staging_version():
    version = make_version(
        record_count=0,
        first_timestamp=None,
        last_timestamp=None,
    )

    assert version.record_count == 0
    assert version.first_timestamp is None
    assert version.last_timestamp is None


def test_dataset_version_allows_checksum_to_be_absent_during_staging():
    version = make_version()

    assert version.checksum_algorithm is None
    assert version.checksum is None


def test_dataset_version_rejects_invalid_dataset_id():
    with pytest.raises(TypeError, match="dataset_id"):
        make_version(dataset_id="invalid")


def test_dataset_version_rejects_boolean_version_number():
    with pytest.raises(TypeError, match="version_number"):
        make_version(version_number=True)


def test_dataset_version_rejects_non_integer_version_number():
    with pytest.raises(TypeError, match="version_number"):
        make_version(version_number=1.0)


def test_dataset_version_rejects_zero_version_number():
    with pytest.raises(ValueError, match="at least 1"):
        make_version(version_number=0)


def test_dataset_version_rejects_negative_record_count():
    with pytest.raises(ValueError, match="cannot be negative"):
        make_version(record_count=-1)


def test_dataset_version_rejects_boolean_record_count():
    with pytest.raises(TypeError, match="record_count"):
        make_version(record_count=True)


def test_dataset_version_rejects_missing_first_timestamp():
    with pytest.raises(ValueError, match="both observation boundaries"):
        make_version(first_timestamp=None)


def test_dataset_version_rejects_missing_last_timestamp():
    with pytest.raises(ValueError, match="both observation boundaries"):
        make_version(last_timestamp=None)


def test_dataset_version_rejects_reversed_boundaries():
    with pytest.raises(ValueError, match="cannot be after"):
        make_version(
            first_timestamp=datetime(
                2026, 1, 1, 9, 2, tzinfo=timezone.utc
            ),
            last_timestamp=datetime(
                2026, 1, 1, 9, 1, tzinfo=timezone.utc
            ),
        )


def test_dataset_version_rejects_boundaries_for_empty_version():
    with pytest.raises(ValueError, match="Empty versions"):
        make_version(
            record_count=0,
            first_timestamp=datetime(
                2026, 1, 1, tzinfo=timezone.utc
            ),
            last_timestamp=datetime(
                2026, 1, 1, tzinfo=timezone.utc
            ),
        )


def test_dataset_version_rejects_naive_timestamp():
    with pytest.raises(ValueError, match="timezone information"):
        make_version(
            first_timestamp=datetime(2026, 1, 1, 9, 0),
        )


def test_dataset_version_rejects_empty_schema_version():
    with pytest.raises(ValueError, match="schema_version"):
        make_version(schema_version="  ")


def test_dataset_version_rejects_empty_storage_format():
    with pytest.raises(ValueError, match="storage_format"):
        make_version(storage_format="  ")


def test_dataset_version_rejects_empty_storage_uri():
    with pytest.raises(ValueError, match="storage_uri"):
        make_version(storage_uri="  ")


def test_dataset_version_requires_checksum_pair():
    with pytest.raises(ValueError, match="provided together"):
        make_version(checksum="abc123")


def test_dataset_version_rejects_empty_checksum():
    with pytest.raises(ValueError, match="checksum cannot be empty"):
        make_version(
            checksum_algorithm="sha256",
            checksum="  ",
        )


# Publication tests


def test_create_available_dataset_version():
    created_at = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)
    published_at = datetime(2026, 1, 1, 8, 5, tzinfo=timezone.utc)

    version = make_version(
        status=DatasetVersionStatus.AVAILABLE,
        storage_uri="s3://atlas/data/version-1.parquet",
        checksum_algorithm="sha256",
        checksum="abc123",
        created_at=created_at,
        published_at=published_at,
    )

    assert version.status == DatasetVersionStatus.AVAILABLE
    assert version.storage_uri == "s3://atlas/data/version-1.parquet"
    assert version.published_at == published_at


def test_available_version_requires_records():
    with pytest.raises(ValueError, match="at least one record"):
        make_version(
            status=DatasetVersionStatus.AVAILABLE,
            record_count=0,
            first_timestamp=None,
            last_timestamp=None,
            storage_uri="s3://bucket/data",
            checksum_algorithm="sha256",
            checksum="abc123",
            published_at=datetime(
                2026, 1, 1, 8, 5, tzinfo=timezone.utc
            ),
        )


def test_available_version_requires_storage_uri():
    with pytest.raises(ValueError, match="storage_uri"):
        make_version(
            status=DatasetVersionStatus.AVAILABLE,
            checksum_algorithm="sha256",
            checksum="abc123",
            published_at=datetime(
                2026, 1, 1, 8, 5, tzinfo=timezone.utc
            ),
        )


def test_available_version_requires_checksum():
    with pytest.raises(ValueError, match="checksum metadata"):
        make_version(
            status=DatasetVersionStatus.AVAILABLE,
            storage_uri="s3://bucket/data",
            published_at=datetime(
                2026, 1, 1, 8, 5, tzinfo=timezone.utc
            ),
        )


def test_available_version_requires_publication_timestamp():
    with pytest.raises(ValueError, match="published_at"):
        make_version(
            status=DatasetVersionStatus.AVAILABLE,
            storage_uri="s3://bucket/data",
            checksum_algorithm="sha256",
            checksum="abc123",
        )


def test_staging_version_rejects_publication_timestamp():
    with pytest.raises(ValueError, match="Only available versions"):
        make_version(
            published_at=datetime(
                2026, 1, 1, 8, 5, tzinfo=timezone.utc
            ),
        )


def test_failed_version_rejects_publication_timestamp():
    with pytest.raises(ValueError, match="Only available versions"):
        make_version(
            status=DatasetVersionStatus.FAILED,
            published_at=datetime(
                2026, 1, 1, 8, 5, tzinfo=timezone.utc
            ),
        )


def test_publication_timestamp_cannot_precede_creation():
    with pytest.raises(ValueError, match="before created_at"):
        make_version(
            status=DatasetVersionStatus.AVAILABLE,
            storage_uri="s3://bucket/data",
            checksum_algorithm="sha256",
            checksum="abc123",
            created_at=datetime(
                2026, 1, 1, 8, 5, tzinfo=timezone.utc
            ),
            published_at=datetime(
                2026, 1, 1, 8, 0, tzinfo=timezone.utc
            ),
        )


def test_dataset_version_is_immutable():
    version = make_version()

    with pytest.raises(FrozenInstanceError):
        version.version_number = 2
