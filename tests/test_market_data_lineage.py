
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from atlas_quant.services.market_data.contracts.lineage import (
    DataLineageContract,
)


def create_lineage(**overrides):
    values = {
        "provider": "Dukascopy",
        "provider_symbol": "EURUSD",
        "source_record_id": "record-001",
        "source_timestamp": datetime(
            2026, 1, 1, 12, 0, tzinfo=timezone.utc
        ),
        "ingestion_timestamp": datetime(
            2026, 1, 1, 12, 1, tzinfo=timezone.utc
        ),
        "provider_schema_version": "1.0",
        "raw_data_uri": "raw/dukascopy/eurusd/2026-01.parquet",
        "raw_data_sha256": "a" * 64,
    }

    values.update(overrides)
    return DataLineageContract(**values)


def test_create_lineage_contract():
    lineage = create_lineage()

    assert lineage.provider == "Dukascopy"
    assert lineage.provider_symbol == "EURUSD"
    assert lineage.source_record_id == "record-001"


def test_normalizes_identifiers():
    lineage = create_lineage(
        provider=" Dukascopy ",
        provider_symbol=" EURUSD ",
        raw_data_uri=" raw/data.parquet ",
    )

    assert lineage.provider == "Dukascopy"
    assert lineage.provider_symbol == "EURUSD"
    assert lineage.raw_data_uri == "raw/data.parquet"


@pytest.mark.parametrize(
    "field",
    [
        "provider",
        "provider_symbol",
        "raw_data_uri",
    ],
)
def test_rejects_empty_required_identifiers(field):
    with pytest.raises(ValidationError):
        create_lineage(**{field: " "})


def test_accepts_missing_optional_identifiers():
    lineage = create_lineage(
        source_record_id=None,
        provider_schema_version=None,
    )

    assert lineage.source_record_id is None
    assert lineage.provider_schema_version is None


def test_rejects_empty_optional_identifiers():
    with pytest.raises(ValidationError):
        create_lineage(source_record_id=" ")

    with pytest.raises(ValidationError):
        create_lineage(provider_schema_version=" ")


@pytest.mark.parametrize(
    "field",
    [
        "source_timestamp",
        "ingestion_timestamp",
    ],
)
def test_rejects_naive_timestamps(field):
    with pytest.raises(ValidationError):
        create_lineage(
            **{field: datetime(2026, 1, 1, 12, 0)}
        )


def test_normalizes_timestamps_to_utc():
    timestamp = datetime(
        2026, 1, 1, 15, 0,
        tzinfo=timezone(timedelta(hours=3)),
    )

    lineage = create_lineage(source_timestamp=timestamp)

    assert lineage.source_timestamp.hour == 12
    assert lineage.source_timestamp.tzinfo == timezone.utc


def test_normalizes_sha256_to_lowercase():
    lineage = create_lineage(
        raw_data_sha256="A" * 64,
    )

    assert lineage.raw_data_sha256 == "a" * 64


@pytest.mark.parametrize(
    "checksum",
    [
        "abc",
        "g" * 64,
        "a" * 63,
        "a" * 65,
    ],
)
def test_rejects_invalid_sha256(checksum):
    with pytest.raises(ValidationError):
        create_lineage(raw_data_sha256=checksum)


def test_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        create_lineage(unexpected_field="value")


def test_lineage_contract_is_immutable():
    lineage = create_lineage()

    with pytest.raises(ValidationError):
        lineage.provider = "OtherProvider"


def test_lineage_json_serialization():
    lineage = create_lineage()

    payload = lineage.model_dump_json()

    assert '"provider":"Dukascopy"' in payload
    assert '"provider_symbol":"EURUSD"' in payload
    assert '"raw_data_sha256":"' + "a" * 64 + '"' in payload