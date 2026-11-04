"""Tests for market data validation report domain models."""

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from atlas_quant.services.market_data.domain.quality import (
    DataQualityReport,
    FindingSeverity,
    QualityFinding,
)
from atlas_quant.services.market_data.domain.validation import (
    ValidationOutcome,
    ValidationReport,
)


def make_report(**overrides):
    values = {
        "validator_name": "OHLCV Validator",
        "validator_version": "1.0",
        "quality_report": DataQualityReport(checked_records=10),
        "ingestion_job_id": uuid4(),
    }
    values.update(overrides)
    return ValidationReport(**values)


def test_create_valid_report():
    report = make_report()

    assert report.outcome == ValidationOutcome.VALID
    assert report.report_version == "1.0"
    assert report.quality_report.checked_records == 10


def test_report_generates_uuid():
    assert isinstance(make_report().report_id, type(uuid4()))


def test_report_normalizes_text_fields():
    report = make_report(
        validator_name="  OHLCV Validator  ",
        validator_version="  2.0  ",
        report_version="  1.1  ",
    )

    assert report.validator_name == "OHLCV Validator"
    assert report.validator_version == "2.0"
    assert report.report_version == "1.1"


@pytest.mark.parametrize(
    "field_name",
    ["validator_name", "validator_version", "report_version"],
)
def test_report_rejects_empty_text(field_name):
    with pytest.raises(ValueError, match="cannot be empty"):
        make_report(**{field_name: "  "})


@pytest.mark.parametrize(
    "field_name",
    ["validator_name", "validator_version", "report_version"],
)
def test_report_rejects_non_string_text(field_name):
    with pytest.raises(TypeError, match="must be a string"):
        make_report(**{field_name: 123})


def test_report_rejects_invalid_report_id():
    with pytest.raises(TypeError, match="report_id"):
        make_report(report_id="invalid")


def test_report_rejects_invalid_ingestion_job_id():
    with pytest.raises(TypeError, match="ingestion_job_id"):
        make_report(ingestion_job_id="invalid")


def test_report_rejects_invalid_dataset_version_id():
    with pytest.raises(TypeError, match="dataset_version_id"):
        make_report(dataset_version_id="invalid")


def test_report_requires_at_least_one_reference():
    with pytest.raises(ValueError, match="must reference"):
        make_report(ingestion_job_id=None)


def test_report_can_reference_dataset_version_only():
    version_id = uuid4()

    report = make_report(
        ingestion_job_id=None,
        dataset_version_id=version_id,
    )

    assert report.dataset_version_id == version_id
    assert report.ingestion_job_id is None


def test_report_can_reference_both_records():
    job_id = uuid4()
    version_id = uuid4()

    report = make_report(
        ingestion_job_id=job_id,
        dataset_version_id=version_id,
    )

    assert report.ingestion_job_id == job_id
    assert report.dataset_version_id == version_id


def test_report_requires_quality_report():
    with pytest.raises(TypeError, match="DataQualityReport"):
        make_report(quality_report="invalid")


def test_report_rejects_naive_timestamp():
    with pytest.raises(ValueError, match="timezone information"):
        make_report(validated_at=datetime(2026, 1, 1))


def test_report_normalizes_timestamp_to_utc():
    offset = timezone(timedelta(hours=3))

    report = make_report(
        validated_at=datetime(2026, 1, 1, 3, tzinfo=offset)
    )

    assert report.validated_at.hour == 0
    assert report.validated_at.utcoffset() == timedelta(0)


def test_report_rejects_non_datetime_timestamp():
    with pytest.raises(TypeError, match="datetime"):
        make_report(validated_at="2026-01-01")


def test_warning_report_has_warning_outcome():
    quality = DataQualityReport(
        checked_records=2,
        findings=(
            QualityFinding(
                code="OUT_OF_ORDER",
                severity=FindingSeverity.WARNING,
                message="Records are not chronological.",
                record_index=1,
            ),
        ),
    )

    report = make_report(quality_report=quality)

    assert report.outcome == ValidationOutcome.VALID_WITH_WARNINGS


def test_error_report_has_invalid_outcome():
    quality = DataQualityReport(
        checked_records=2,
        findings=(
            QualityFinding(
                code="DUPLICATE_TIMESTAMP",
                severity=FindingSeverity.ERROR,
                message="Duplicate timestamp detected.",
                record_index=1,
            ),
        ),
    )

    report = make_report(quality_report=quality)

    assert report.outcome == ValidationOutcome.INVALID


def test_info_only_report_is_valid():
    quality = DataQualityReport(
        checked_records=1,
        findings=(
            QualityFinding(
                code="ZERO_WIDTH_SPREAD",
                severity=FindingSeverity.INFO,
                message="Bid and ask are equal.",
                record_index=0,
            ),
        ),
    )

    report = make_report(quality_report=quality)

    assert report.outcome == ValidationOutcome.VALID


def test_report_reuses_quality_report_without_copying():
    quality = DataQualityReport(checked_records=5)

    report = make_report(quality_report=quality)

    assert report.quality_report is quality


def test_report_is_immutable():
    report = make_report()

    with pytest.raises(FrozenInstanceError):
        report.validator_name = "Changed"
