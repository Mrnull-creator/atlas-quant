
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from atlas_quant.services.market_data.application.ohlcv_validator import (
    OHLCVBatchValidator,
)
from atlas_quant.services.market_data.domain.bars import OHLCVBar
from atlas_quant.services.market_data.domain.enums import Timeframe
from atlas_quant.services.market_data.domain.quality import (
    FindingSeverity,
)


@pytest.fixture
def validator():
    return OHLCVBatchValidator()


def make_bar(
    timestamp: datetime,
    *,
    symbol: str = "EURUSD",
    timeframe: Timeframe = Timeframe.M1,
) -> OHLCVBar:
    return OHLCVBar(
        symbol=symbol,
        timeframe=timeframe,
        timestamp=timestamp,
        open=Decimal("1.1000"),
        high=Decimal("1.1010"),
        low=Decimal("1.0990"),
        close=Decimal("1.1005"),
        volume=Decimal("100"),
    )


@pytest.fixture
def timestamps():
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)

    return (
        base,
        base + timedelta(minutes=1),
        base + timedelta(minutes=2),
        base + timedelta(minutes=3),
    )


def test_empty_batch_is_valid(validator):
    report = validator.validate([])

    assert report.checked_records == 0
    assert report.findings == ()
    assert report.is_valid is True


def test_chronological_batch_has_no_findings(
    validator,
    timestamps,
):
    bars = [
        make_bar(timestamp)
        for timestamp in timestamps
    ]

    report = validator.validate(bars)

    assert report.checked_records == 4
    assert report.findings == ()
    assert report.is_valid is True


def test_detect_duplicate_timestamp(
    validator,
    timestamps,
):
    bars = [
        make_bar(timestamps[0]),
        make_bar(timestamps[1]),
        make_bar(timestamps[1]),
    ]

    report = validator.validate(bars)

    assert report.checked_records == 3
    assert len(report.findings) == 1

    finding = report.findings[0]

    assert finding.code == "DUPLICATE_BAR_TIMESTAMP"
    assert finding.severity == FindingSeverity.ERROR
    assert finding.record_index == 2
    assert finding.field == "timestamp"


def test_duplicate_detection_is_instrument_specific(
    validator,
    timestamps,
):
    bars = [
        make_bar(timestamps[0], symbol="EURUSD"),
        make_bar(timestamps[0], symbol="GBPUSD"),
    ]

    report = validator.validate(bars)

    assert report.findings == ()
    assert report.is_valid is True


def test_duplicate_detection_is_timeframe_specific(
    validator,
    timestamps,
):
    bars = [
        make_bar(
            timestamps[0],
            timeframe=Timeframe.M1,
        ),
        make_bar(
            timestamps[0],
            timeframe=Timeframe.M5,
        ),
    ]

    report = validator.validate(bars)

    assert report.findings == ()
    assert report.is_valid is True


def test_detect_out_of_order_observation(
    validator,
    timestamps,
):
    bars = [
        make_bar(timestamps[0]),
        make_bar(timestamps[2]),
        make_bar(timestamps[1]),
    ]

    report = validator.validate(bars)

    assert len(report.findings) == 1

    finding = report.findings[0]

    assert finding.code == "OUT_OF_ORDER_BAR"
    assert finding.severity == FindingSeverity.WARNING
    assert finding.record_index == 2


def test_out_of_order_observation_is_not_removed(
    validator,
    timestamps,
):
    bars = [
        make_bar(timestamps[2]),
        make_bar(timestamps[0]),
        make_bar(timestamps[1]),
    ]

    report = validator.validate(bars)

    assert report.checked_records == 3
    assert report.findings[0].record_index == 1


def test_validate_series_independently(
    validator,
    timestamps,
):
    bars = [
        make_bar(timestamps[0], symbol="EURUSD"),
        make_bar(timestamps[0], symbol="GBPUSD"),
        make_bar(timestamps[1], symbol="EURUSD"),
        make_bar(timestamps[1], symbol="GBPUSD"),
    ]

    report = validator.validate(bars)

    assert report.findings == ()
    assert report.is_valid is True


def test_detect_multiple_quality_issues(
    validator,
    timestamps,
):
    bars = [
        make_bar(timestamps[0]),
        make_bar(timestamps[2]),
        make_bar(timestamps[1]),
        make_bar(timestamps[1]),
    ]

    report = validator.validate(bars)

    assert report.error_count == 1
    assert report.warning_count == 1
    assert report.is_valid is False
    assert report.has_warnings is True


def test_findings_preserve_original_record_indexes(
    validator,
    timestamps,
):
    bars = [
        make_bar(timestamps[0]),
        make_bar(timestamps[2]),
        make_bar(timestamps[1]),
        make_bar(timestamps[1]),
    ]

    report = validator.validate(bars)

    assert [
        finding.record_index
        for finding in report.findings
    ] == [2, 3]


def test_reject_invalid_observation_type(validator):
    with pytest.raises(TypeError):
        validator.validate(["not a bar"])


def test_reject_non_iterable_input(validator):
    with pytest.raises(TypeError):
        validator.validate(None)


def test_reject_string_input(validator):
    with pytest.raises(TypeError):
        validator.validate("invalid")


def test_accept_generator_input(
    validator,
    timestamps,
):
    bars = (
        make_bar(timestamp)
        for timestamp in timestamps
    )

    report = validator.validate(bars)

    assert report.checked_records == 4
    assert report.is_valid is True


def test_validation_does_not_modify_input_order(
    validator,
    timestamps,
):
    bars = [
        make_bar(timestamps[2]),
        make_bar(timestamps[0]),
        make_bar(timestamps[1]),
    ]

    original_order = [
        bar.timestamp
        for bar in bars
    ]

    validator.validate(bars)

    assert [
        bar.timestamp
        for bar in bars
    ] == original_order
def test_finding_details_are_bounded_but_aggregate_counts_are_preserved(
    timestamps,
):
    validator = OHLCVBatchValidator(max_findings=1)

    bars = [
        make_bar(timestamps[0]),
        make_bar(timestamps[3]),
        make_bar(timestamps[1]),
        make_bar(timestamps[1]),
        make_bar(timestamps[2]),
    ]

    report = validator.validate(bars)

    assert report.checked_records == 5
    assert len(report.findings) == 1
    assert report.retained_finding_count == 1
    assert report.total_findings == 3
    assert report.error_count == 1
    assert report.warning_count == 2
    assert report.info_count == 0
    assert report.findings_truncated is True
    assert report.is_valid is False
    assert report.has_warnings is True


def test_session_add_returns_error_status(timestamps):
    session = OHLCVBatchValidator().start_session()

    try:
        assert session.add(make_bar(timestamps[0])) is False
        assert session.add(make_bar(timestamps[0])) is True
        assert session.add(make_bar(timestamps[2])) is False
    finally:
        session.close()
