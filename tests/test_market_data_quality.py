from datetime import datetime, timedelta, timezone

import pytest

from atlas_quant.services.market_data.domain.quality import (
    DataQualityReport,
    FindingSeverity,
    QualityFinding,
)


def create_finding(
    severity: FindingSeverity = FindingSeverity.ERROR,
    **overrides,
) -> QualityFinding:
    values = {
        "code": "missing_timestamp",
        "severity": severity,
        "message": "A timestamp is missing.",
        "record_index": 0,
        "field": "timestamp",
    }

    values.update(overrides)

    return QualityFinding(**values)


def test_create_quality_finding():
    finding = create_finding()

    assert finding.code == "MISSING_TIMESTAMP"
    assert finding.severity == FindingSeverity.ERROR
    assert finding.record_index == 0
    assert finding.field == "timestamp"


def test_normalize_finding_code():
    finding = create_finding(code="  invalid_price  ")

    assert finding.code == "INVALID_PRICE"


def test_normalize_finding_message():
    finding = create_finding(message="  Invalid price.  ")

    assert finding.message == "Invalid price."


def test_reject_empty_finding_code():
    with pytest.raises(ValueError):
        create_finding(code="  ")


def test_reject_empty_finding_message():
    with pytest.raises(ValueError):
        create_finding(message="  ")


def test_reject_invalid_severity():
    with pytest.raises(TypeError):
        create_finding(severity="error")


def test_reject_negative_record_index():
    with pytest.raises(ValueError):
        create_finding(record_index=-1)


def test_reject_boolean_record_index():
    with pytest.raises(TypeError):
        create_finding(record_index=True)


def test_reject_empty_field():
    with pytest.raises(ValueError):
        create_finding(field="  ")


def test_reject_naive_finding_timestamp():
    with pytest.raises(ValueError):
        create_finding(timestamp=datetime(2026, 1, 1))


def test_normalize_finding_timestamp_to_utc():
    timestamp = datetime(
        2026,
        1,
        1,
        12,
        tzinfo=timezone(timedelta(hours=3)),
    )

    finding = create_finding(timestamp=timestamp)

    assert finding.timestamp.utcoffset() == timedelta(0)
    assert finding.timestamp.hour == 9


def test_create_empty_quality_report():
    report = DataQualityReport(checked_records=10)

    assert report.checked_records == 10
    assert report.findings == ()
    assert report.error_count == 0
    assert report.warning_count == 0
    assert report.info_count == 0
    assert report.is_valid is True
    assert report.has_warnings is False


def test_calculate_quality_report_counts():
    report = DataQualityReport(
        checked_records=3,
        findings=(
            create_finding(
                FindingSeverity.ERROR,
                record_index=0,
            ),
            create_finding(
                FindingSeverity.WARNING,
                code="UNUSUAL_SPREAD",
                record_index=1,
            ),
            create_finding(
                FindingSeverity.INFO,
                code="DATA_GAP",
                record_index=2,
            ),
        ),
    )

    assert report.error_count == 1
    assert report.warning_count == 1
    assert report.info_count == 1
    assert report.is_valid is False
    assert report.has_warnings is True


def test_report_with_warnings_remains_valid():
    report = DataQualityReport(
        checked_records=1,
        findings=(
            create_finding(FindingSeverity.WARNING),
        ),
    )

    assert report.is_valid is True
    assert report.has_warnings is True


def test_reject_negative_checked_records():
    with pytest.raises(ValueError):
        DataQualityReport(checked_records=-1)


def test_reject_boolean_checked_records():
    with pytest.raises(TypeError):
        DataQualityReport(checked_records=True)


def test_reject_invalid_findings_collection():
    with pytest.raises(TypeError):
        DataQualityReport(
            checked_records=1,
            findings="invalid",
        )


def test_reject_invalid_finding_object():
    with pytest.raises(TypeError):
        DataQualityReport(
            checked_records=1,
            findings=("invalid",),
        )


def test_reject_finding_outside_record_range():
    with pytest.raises(ValueError):
        DataQualityReport(
            checked_records=2,
            findings=(
                create_finding(record_index=2),
            ),
        )


def test_allow_dataset_level_finding():
    finding = create_finding(record_index=None)

    report = DataQualityReport(
        checked_records=10,
        findings=(finding,),
    )

    assert report.findings[0].record_index is None


def test_report_normalizes_findings_to_tuple():
    report = DataQualityReport(
        checked_records=1,
        findings=[
            create_finding(),
        ],
    )

    assert isinstance(report.findings, tuple)


def test_quality_finding_is_immutable():
    finding = create_finding()

    with pytest.raises(AttributeError):
        finding.code = "CHANGED"


def test_quality_report_is_immutable():
    report = DataQualityReport(checked_records=10)

    with pytest.raises(AttributeError):
        report.checked_records = 20

def test_report_preserves_total_counts_when_details_are_truncated():
    report = DataQualityReport(
        checked_records=10,
        findings=(
            create_finding(FindingSeverity.ERROR, record_index=0),
        ),
        total_error_count=25,
        total_warning_count=12,
        total_info_count=3,
    )

    assert report.error_count == 25
    assert report.warning_count == 12
    assert report.info_count == 3
    assert report.total_findings == 40
    assert report.retained_finding_count == 1
    assert report.findings_truncated is True
    assert report.is_valid is False
    assert report.has_warnings is True


def test_report_without_explicit_totals_remains_backward_compatible():
    report = DataQualityReport(
        checked_records=2,
        findings=(
            create_finding(FindingSeverity.ERROR, record_index=0),
            create_finding(FindingSeverity.WARNING, record_index=1),
        ),
    )

    assert report.error_count == 1
    assert report.warning_count == 1
    assert report.info_count == 0
    assert report.findings_truncated is False


def test_report_rejects_totals_smaller_than_retained_findings():
    with pytest.raises(ValueError, match="less than retained"):
        DataQualityReport(
            checked_records=1,
            findings=(create_finding(),),
            total_error_count=0,
        )


def test_report_rejects_negative_aggregate_count():
    with pytest.raises(ValueError):
        DataQualityReport(
            checked_records=1,
            total_error_count=-1,
        )


def test_report_rejects_invalid_truncation_flag():
    with pytest.raises(TypeError):
        DataQualityReport(
            checked_records=1,
            findings_truncated=1,
        )


def test_finding_accumulator_retains_bounded_details_and_counts_all():
    from atlas_quant.services.market_data.domain.quality import (
        QualityFindingAccumulator,
    )

    accumulator = QualityFindingAccumulator(max_findings=2)

    for index in range(5):
        accumulator.add(
            create_finding(
                FindingSeverity.ERROR,
                record_index=index,
            )
        )

    accumulator.add(
        create_finding(
            FindingSeverity.WARNING,
            record_index=5,
        )
    )

    report = accumulator.build_report(checked_records=6)

    assert len(report.findings) == 2
    assert report.error_count == 5
    assert report.warning_count == 1
    assert report.total_findings == 6
    assert report.findings_truncated is True
    assert report.is_valid is False


def test_finding_accumulator_merges_report_totals_without_double_counting():
    from atlas_quant.services.market_data.domain.quality import (
        QualityFindingAccumulator,
    )

    original = DataQualityReport(
        checked_records=5,
        findings=(
            create_finding(FindingSeverity.ERROR, record_index=0),
        ),
        total_error_count=10,
        total_warning_count=4,
    )

    accumulator = QualityFindingAccumulator(max_findings=3)
    accumulator.merge_report(original)
    accumulator.add(
        create_finding(FindingSeverity.INFO, record_index=1)
    )

    report = accumulator.build_report(checked_records=5)

    assert len(report.findings) == 2
    assert report.error_count == 10
    assert report.warning_count == 4
    assert report.info_count == 1
    assert report.total_findings == 15
    assert report.findings_truncated is True


@pytest.mark.parametrize(
    "value",
    [0, -1],
)
def test_finding_accumulator_rejects_nonpositive_limit(value):
    from atlas_quant.services.market_data.domain.quality import (
        QualityFindingAccumulator,
    )

    with pytest.raises(ValueError):
        QualityFindingAccumulator(max_findings=value)


def test_finding_accumulator_rejects_boolean_limit():
    from atlas_quant.services.market_data.domain.quality import (
        QualityFindingAccumulator,
    )

    with pytest.raises(TypeError):
        QualityFindingAccumulator(max_findings=True)