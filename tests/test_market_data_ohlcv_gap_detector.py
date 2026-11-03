from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from atlas_quant.services.market_data.application.ohlcv_gap_detector import (
    OHLCVGapDetector,
)
from atlas_quant.services.market_data.domain.bars import OHLCVBar
from atlas_quant.services.market_data.domain.enums import Timeframe
from atlas_quant.services.market_data.domain.quality import (
    FindingSeverity,
)


BASE_TIME = datetime(
    2026, 1, 1, 10, 0, tzinfo=timezone.utc
)


def create_bar(
    *,
    symbol="EURUSD",
    timeframe=Timeframe.M1,
    timestamp=BASE_TIME,
):
    return OHLCVBar(
        symbol=symbol,
        timeframe=timeframe,
        timestamp=timestamp,
        open=Decimal("1.10000"),
        high=Decimal("1.10100"),
        low=Decimal("1.09900"),
        close=Decimal("1.10050"),
        volume=Decimal("100"),
    )


def create_detector():
    return OHLCVGapDetector()


def minute_offset(minutes):
    return BASE_TIME + timedelta(minutes=minutes)


def test_complete_series_has_no_gaps():
    bars = [
        create_bar(timestamp=minute_offset(0)),
        create_bar(timestamp=minute_offset(1)),
        create_bar(timestamp=minute_offset(2)),
    ]

    expected = [
        minute_offset(0),
        minute_offset(1),
        minute_offset(2),
    ]

    report = create_detector().detect(bars, expected)

    assert report.checked_records == 3
    assert report.findings == ()
    assert report.is_valid
    assert not report.has_warnings


def test_detects_one_missing_bar():
    bars = [
        create_bar(timestamp=minute_offset(0)),
        create_bar(timestamp=minute_offset(2)),
    ]

    expected = [
        minute_offset(0),
        minute_offset(1),
        minute_offset(2),
    ]

    report = create_detector().detect(bars, expected)

    assert report.checked_records == 2
    assert len(report.findings) == 1

    finding = report.findings[0]

    assert finding.code == "MISSING_OHLCV_BAR"
    assert finding.severity == FindingSeverity.WARNING
    assert finding.timestamp == minute_offset(1)
    assert finding.field == "timestamp"
    assert finding.record_index is None

    assert report.is_valid
    assert report.has_warnings


def test_detects_multiple_missing_bars():
    bars = [
        create_bar(timestamp=minute_offset(0)),
        create_bar(timestamp=minute_offset(4)),
    ]

    expected = [
        minute_offset(0),
        minute_offset(1),
        minute_offset(2),
        minute_offset(3),
        minute_offset(4),
    ]

    report = create_detector().detect(bars, expected)

    assert [
        finding.timestamp for finding in report.findings
    ] == [
        minute_offset(1),
        minute_offset(2),
        minute_offset(3),
    ]


def test_missing_timestamps_are_reported_chronologically():
    bars = [
        create_bar(timestamp=minute_offset(0)),
    ]

    expected = [
        minute_offset(3),
        minute_offset(1),
        minute_offset(2),
    ]

    report = create_detector().detect(bars, expected)

    assert [
        finding.timestamp for finding in report.findings
    ] == [
        minute_offset(1),
        minute_offset(2),
        minute_offset(3),
    ]


def test_empty_observations_report_all_expected_timestamps_missing():
    expected = [
        minute_offset(0),
        minute_offset(1),
    ]

    report = create_detector().detect([], expected)

    assert report.checked_records == 0
    assert len(report.findings) == 2
    assert report.error_count == 0
    assert report.warning_count == 2


def test_empty_expected_timestamps_report_no_gaps():
    bars = [
        create_bar(),
    ]

    report = create_detector().detect(bars, [])

    assert report.checked_records == 1
    assert report.findings == ()


def test_empty_observations_and_expectations_are_valid():
    report = create_detector().detect([], [])

    assert report.checked_records == 0
    assert report.findings == ()
    assert report.is_valid


def test_ignores_observed_timestamps_not_in_expected_schedule():
    bars = [
        create_bar(timestamp=minute_offset(0)),
        create_bar(timestamp=minute_offset(1)),
        create_bar(timestamp=minute_offset(5)),
    ]

    expected = [
        minute_offset(0),
        minute_offset(1),
    ]

    report = create_detector().detect(bars, expected)

    assert report.findings == ()
    assert report.checked_records == 3


def test_accepts_generator_inputs():
    bars = (
        create_bar(timestamp=minute_offset(index))
        for index in (0, 2)
    )

    expected = (
        minute_offset(index)
        for index in range(3)
    )

    report = create_detector().detect(bars, expected)

    assert report.checked_records == 2
    assert len(report.findings) == 1
    assert report.findings[0].timestamp == minute_offset(1)


def test_accepts_timezone_equivalent_timestamps():
    local_timestamp = datetime(
        2026,
        1,
        1,
        13,
        0,
        tzinfo=timezone(timedelta(hours=3)),
    )

    bars = [create_bar(timestamp=BASE_TIME)]

    report = create_detector().detect(
        bars,
        [local_timestamp],
    )

    assert report.findings == ()


def test_normalizes_missing_timestamps_to_utc():
    local_timestamp = datetime(
        2026,
        1,
        1,
        13,
        1,
        tzinfo=timezone(timedelta(hours=3)),
    )

    report = create_detector().detect(
        [],
        [local_timestamp],
    )

    assert report.findings[0].timestamp == minute_offset(1)
    assert report.findings[0].timestamp.tzinfo == timezone.utc


def test_rejects_duplicate_expected_timestamps():
    equivalent_timestamp = datetime(
        2026,
        1,
        1,
        13,
        0,
        tzinfo=timezone(timedelta(hours=3)),
    )

    with pytest.raises(ValueError, match="duplicates"):
        create_detector().detect(
            [],
            [BASE_TIME, equivalent_timestamp],
        )


def test_rejects_naive_expected_timestamp():
    with pytest.raises(ValueError, match="timezone"):
        create_detector().detect(
            [],
            [datetime(2026, 1, 1, 10, 0)],
        )


def test_rejects_non_datetime_expected_timestamp():
    with pytest.raises(TypeError, match="datetime"):
        create_detector().detect([], ["2026-01-01T10:00:00Z"])


def test_rejects_non_iterable_expected_timestamps():
    with pytest.raises(TypeError, match="iterable"):
        create_detector().detect([], None)


def test_rejects_non_iterable_bars():
    with pytest.raises(TypeError, match="iterable"):
        create_detector().detect(None, [])


@pytest.mark.parametrize("invalid_input", ["bars", b"bars"])
def test_rejects_string_bar_input(invalid_input):
    with pytest.raises(TypeError, match="OHLCVBar"):
        create_detector().detect(invalid_input, [])


def test_rejects_non_bar_observations():
    with pytest.raises(TypeError, match="OHLCVBar"):
        create_detector().detect([object()], [])


def test_rejects_mixed_instruments():
    bars = [
        create_bar(symbol="EURUSD"),
        create_bar(symbol="GBPUSD", timestamp=minute_offset(1)),
    ]

    with pytest.raises(ValueError, match="same instrument"):
        create_detector().detect(bars, [])


def test_rejects_mixed_timeframes():
    bars = [
        create_bar(timeframe=Timeframe.M1),
        create_bar(
            timeframe=Timeframe.M5,
            timestamp=minute_offset(1),
        ),
    ]

    with pytest.raises(ValueError, match="same instrument"):
        create_detector().detect(bars, [])


def test_does_not_modify_or_sort_observations():
    bars = [
        create_bar(timestamp=minute_offset(2)),
        create_bar(timestamp=minute_offset(0)),
    ]

    original_order = tuple(bars)

    create_detector().detect(
        bars,
        [
            minute_offset(0),
            minute_offset(1),
            minute_offset(2),
        ],
    )

    assert tuple(bars) == original_order