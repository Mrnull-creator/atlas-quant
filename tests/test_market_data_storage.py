from datetime import datetime, timedelta, timezone

import pytest

from atlas_quant.services.market_data.application.storage import (
    StorageObjectMetadata,
)


def make_metadata(**overrides) -> StorageObjectMetadata:
    values = {
        "storage_uri": "storage://market-data/dataset-001",
        "storage_format": "PARQUET",
        "checksum_algorithm": "SHA256",
        "checksum": "a" * 64,
        "size_bytes": 1024,
        "record_count": 100,
        "first_timestamp": datetime(
            2026, 1, 1, tzinfo=timezone.utc
        ),
        "last_timestamp": datetime(
            2026, 1, 2, tzinfo=timezone.utc
        ),
    }
    values.update(overrides)
    return StorageObjectMetadata(**values)


def test_storage_metadata_normalizes_text():
    metadata = make_metadata()

    assert metadata.storage_format == "parquet"
    assert metadata.checksum_algorithm == "sha256"


def test_storage_metadata_normalizes_timestamps_to_utc():
    local_timestamp = datetime(
        2026, 1, 1, 3, tzinfo=timezone(timedelta(hours=3))
    )

    metadata = make_metadata(
        first_timestamp=local_timestamp,
        last_timestamp=local_timestamp,
    )

    assert metadata.first_timestamp.tzinfo == timezone.utc
    assert metadata.first_timestamp.hour == 0


def test_storage_metadata_accepts_missing_record_details():
    metadata = make_metadata(
        record_count=None,
        first_timestamp=None,
        last_timestamp=None,
    )

    assert metadata.record_count is None
    assert metadata.first_timestamp is None
    assert metadata.last_timestamp is None


def test_storage_metadata_rejects_empty_required_text():
    with pytest.raises(ValueError):
        make_metadata(storage_uri="   ")


def test_storage_metadata_rejects_non_string_required_text():
    with pytest.raises(TypeError):
        make_metadata(storage_format=None)


def test_storage_metadata_rejects_negative_size():
    with pytest.raises(ValueError):
        make_metadata(size_bytes=-1)


def test_storage_metadata_rejects_boolean_size():
    with pytest.raises(TypeError):
        make_metadata(size_bytes=True)


def test_storage_metadata_rejects_negative_record_count():
    with pytest.raises(ValueError):
        make_metadata(record_count=-1)


def test_storage_metadata_rejects_boolean_record_count():
    with pytest.raises(TypeError):
        make_metadata(record_count=True)


def test_storage_metadata_requires_both_observation_boundaries():
    with pytest.raises(ValueError):
        make_metadata(last_timestamp=None)


def test_storage_metadata_rejects_naive_timestamps():
    with pytest.raises(ValueError):
        make_metadata(
            first_timestamp=datetime(2026, 1, 1),
        )


def test_storage_metadata_rejects_reversed_boundaries():
    with pytest.raises(ValueError):
        make_metadata(
            first_timestamp=datetime(
                2026, 1, 3, tzinfo=timezone.utc
            ),
            last_timestamp=datetime(
                2026, 1, 2, tzinfo=timezone.utc
            ),
        )


def test_empty_storage_object_cannot_have_observation_boundaries():
    with pytest.raises(ValueError):
        make_metadata(
            record_count=0,
        )