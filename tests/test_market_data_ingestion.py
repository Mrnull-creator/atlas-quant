
"""Tests for market data ingestion job domain models."""

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from atlas_quant.services.market_data.domain.datasets import (
    DatasetVersionStatus,
)
from atlas_quant.services.market_data.domain.enums import (
    AssetClass,
    Timeframe,
)
from atlas_quant.services.market_data.domain.ingestion import (
    IngestionJob,
    IngestionJobStatus,
)


def make_job(**overrides):
    values = {
        "provider_id": "dukascopy",
        "asset_class": AssetClass.FOREX,
        "instrument_symbol": "EUR/USD",
        "timeframe": Timeframe.M1,
        "requested_start": datetime(
            2026, 1, 1, tzinfo=timezone.utc
        ),
        "requested_end": datetime(
            2026, 1, 2, tzinfo=timezone.utc
        ),
        "created_at": datetime(
            2026, 1, 1, tzinfo=timezone.utc
        ),
    }
    values.update(overrides)
    return IngestionJob(**values)


def test_create_pending_job():
    job = make_job()

    assert job.status == IngestionJobStatus.PENDING
    assert job.provider_id == "dukascopy"
    assert job.instrument_symbol == "EUR/USD"
    assert job.records_downloaded == 0
    assert job.produced_dataset_version_ids == ()


def test_job_generates_uuid():
    assert isinstance(make_job().job_id, type(uuid4()))


def test_provider_id_is_normalized():
    job = make_job(provider_id="  Dukascopy  ")

    assert job.provider_id == "dukascopy"


def test_instrument_symbol_is_normalized():
    job = make_job(instrument_symbol=" eur/usd ")

    assert job.instrument_symbol == "EUR/USD"


def test_job_supports_optional_instrument_scope():
    job = make_job(instrument_symbol=None, timeframe=None)

    assert job.instrument_symbol is None
    assert job.timeframe is None


def test_job_rejects_empty_provider():
    with pytest.raises(ValueError, match="provider_id"):
        make_job(provider_id="  ")


def test_job_rejects_invalid_provider_format():
    with pytest.raises(ValueError, match="invalid format"):
        make_job(provider_id="provider name")


def test_job_rejects_invalid_asset_class():
    with pytest.raises(TypeError, match="AssetClass"):
        make_job(asset_class="forex")


def test_job_rejects_invalid_timeframe():
    with pytest.raises(TypeError, match="Timeframe"):
        make_job(timeframe="M1")


def test_job_rejects_invalid_status():
    with pytest.raises(TypeError, match="IngestionJobStatus"):
        make_job(status="pending")


def test_job_rejects_invalid_job_id():
    with pytest.raises(TypeError, match="job_id"):
        make_job(job_id="invalid")


def test_job_rejects_empty_instrument_symbol():
    with pytest.raises(ValueError, match="instrument_symbol"):
        make_job(instrument_symbol="  ")


def test_requested_timestamps_are_normalized_to_utc():
    offset = timezone(timedelta(hours=3))

    job = make_job(
        requested_start=datetime(2026, 1, 1, 3, tzinfo=offset),
        requested_end=datetime(2026, 1, 2, 3, tzinfo=offset),
    )

    assert job.requested_start.hour == 0
    assert job.requested_end.hour == 0
    assert job.requested_start.utcoffset() == timedelta(0)


def test_job_rejects_naive_requested_timestamp():
    with pytest.raises(ValueError, match="timezone information"):
        make_job(requested_start=datetime(2026, 1, 1))


def test_job_rejects_reversed_requested_range():
    with pytest.raises(ValueError, match="before"):
        make_job(
            requested_start=datetime(
                2026, 1, 2, tzinfo=timezone.utc
            ),
            requested_end=datetime(
                2026, 1, 1, tzinfo=timezone.utc
            ),
        )


@pytest.mark.parametrize(
    "field_name",
    [
        "records_downloaded",
        "records_written",
        "records_rejected",
    ],
)
def test_job_rejects_negative_counts(field_name):
    with pytest.raises(ValueError, match="cannot be negative"):
        make_job(**{field_name: -1})


@pytest.mark.parametrize(
    "field_name",
    [
        "records_downloaded",
        "records_written",
        "records_rejected",
    ],
)
def test_job_rejects_boolean_counts(field_name):
    with pytest.raises(TypeError, match="must be an integer"):
        make_job(**{field_name: True})


def test_job_accepts_produced_dataset_version_ids():
    version_id = uuid4()

    job = make_job(
        produced_dataset_version_ids=(version_id,)
    )

    assert job.produced_dataset_version_ids == (version_id,)


def test_job_rejects_non_tuple_version_ids():
    with pytest.raises(TypeError, match="must be a tuple"):
        make_job(produced_dataset_version_ids=[uuid4()])


def test_job_rejects_invalid_version_id():
    with pytest.raises(TypeError, match="must be a UUID"):
        make_job(produced_dataset_version_ids=("invalid",))


def test_job_rejects_duplicate_version_ids():
    version_id = uuid4()

    with pytest.raises(ValueError, match="must be unique"):
        make_job(
            produced_dataset_version_ids=(version_id, version_id)
        )


def test_running_job_requires_started_at():
    with pytest.raises(ValueError, match="require started_at"):
        make_job(status=IngestionJobStatus.RUNNING)


def test_create_running_job():
    started_at = datetime(
        2026, 1, 1, 0, 5, tzinfo=timezone.utc
    )

    job = make_job(
        status=IngestionJobStatus.RUNNING,
        started_at=started_at,
    )

    assert job.status == IngestionJobStatus.RUNNING
    assert job.started_at == started_at


def test_running_job_cannot_have_completion_time():
    with pytest.raises(ValueError, match="cannot have completed_at"):
        make_job(
            status=IngestionJobStatus.RUNNING,
            started_at=datetime(
                2026, 1, 1, 0, 5, tzinfo=timezone.utc
            ),
            completed_at=datetime(
                2026, 1, 1, 0, 10, tzinfo=timezone.utc
            ),
        )


def test_succeeded_job_requires_completion_time():
    with pytest.raises(ValueError, match="require completed_at"):
        make_job(status=IngestionJobStatus.SUCCEEDED)


def test_create_succeeded_job():
    completed_at = datetime(
        2026, 1, 1, 0, 10, tzinfo=timezone.utc
    )

    job = make_job(
        status=IngestionJobStatus.SUCCEEDED,
        started_at=datetime(
            2026, 1, 1, 0, 5, tzinfo=timezone.utc
        ),
        completed_at=completed_at,
        records_downloaded=100,
        records_written=100,
    )

    assert job.status == IngestionJobStatus.SUCCEEDED
    assert job.records_downloaded == 100


def test_failed_job_requires_failure_message():
    with pytest.raises(ValueError, match="require failure_message"):
        make_job(
            status=IngestionJobStatus.FAILED,
            completed_at=datetime(
                2026, 1, 1, 0, 10, tzinfo=timezone.utc
            ),
        )


def test_create_failed_job():
    job = make_job(
        status=IngestionJobStatus.FAILED,
        completed_at=datetime(
            2026, 1, 1, 0, 10, tzinfo=timezone.utc
        ),
        failure_code="PROVIDER_TIMEOUT",
        failure_message="Provider did not respond.",
    )

    assert job.status == IngestionJobStatus.FAILED
    assert job.failure_code == "PROVIDER_TIMEOUT"


def test_partially_succeeded_job_requires_output():
    with pytest.raises(ValueError, match="at least one"):
        make_job(
            status=IngestionJobStatus.PARTIALLY_SUCCEEDED,
            completed_at=datetime(
                2026, 1, 1, 0, 10, tzinfo=timezone.utc
            ),
            failure_message="One dataset could not be published.",
        )


def test_create_partially_succeeded_job():
    version_id = uuid4()

    job = make_job(
        status=IngestionJobStatus.PARTIALLY_SUCCEEDED,
        completed_at=datetime(
            2026, 1, 1, 0, 10, tzinfo=timezone.utc
        ),
        produced_dataset_version_ids=(version_id,),
        failure_message="Raw data succeeded; normalized data failed.",
    )

    assert job.status == IngestionJobStatus.PARTIALLY_SUCCEEDED
    assert version_id in job.produced_dataset_version_ids


def test_create_cancelled_job():
    job = make_job(
        status=IngestionJobStatus.CANCELLED,
        completed_at=datetime(
            2026, 1, 1, 0, 10, tzinfo=timezone.utc
        ),
    )

    assert job.status == IngestionJobStatus.CANCELLED


def test_nonterminal_job_rejects_failure_details():
    with pytest.raises(ValueError, match="failure details"):
        make_job(failure_message="Unexpected failure")


def test_job_rejects_started_at_before_creation():
    with pytest.raises(ValueError, match="before created_at"):
        make_job(
            created_at=datetime(
                2026, 1, 1, 0, 10, tzinfo=timezone.utc
            ),
            status=IngestionJobStatus.RUNNING,
            started_at=datetime(
                2026, 1, 1, 0, 5, tzinfo=timezone.utc
            ),
        )


def test_job_rejects_completion_before_start():
    with pytest.raises(ValueError, match="before started_at"):
        make_job(
            status=IngestionJobStatus.SUCCEEDED,
            started_at=datetime(
                2026, 1, 1, 0, 10, tzinfo=timezone.utc
            ),
            completed_at=datetime(
                2026, 1, 1, 0, 5, tzinfo=timezone.utc
            ),
        )


def test_job_is_immutable():
    job = make_job()

    with pytest.raises(FrozenInstanceError):
        job.status = IngestionJobStatus.RUNNING