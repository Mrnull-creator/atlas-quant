"""Tests for OHLCV market data coverage assessment."""

from datetime import datetime, time, timezone
from decimal import Decimal

import pytest

from atlas_quant.services.market_data.application.ohlcv_coverage import (
    OHLCVCoverageAssessor,
    OHLCVCoverageAssessment,
)
from atlas_quant.services.market_data.domain.bar_boundaries import (
    BarAlignmentMode,
    BarBoundaryPolicy,
)
from atlas_quant.services.market_data.domain.bars import OHLCVBar
from atlas_quant.services.market_data.domain.calendar import (
    SessionWindow,
    TradingCalendar,
    WeeklySession,
)
from atlas_quant.services.market_data.domain.enums import (
    AssetClass,
    Timeframe,
)
from atlas_quant.services.market_data.domain.instruments import Instrument
from atlas_quant.services.market_data.domain.provider_availability import (
    ProviderAvailabilitySchedule,
    ProviderAvailabilityWindow,
    WeeklyProviderAvailability,
)
from atlas_quant.services.market_data.domain.quality import (
    DataQualityReport,
)


UTC = timezone.utc


def make_instrument(symbol="EURUSD"):
    return Instrument(
        symbol=symbol,
        asset_class=AssetClass.FOREX,
    )


def make_calendar():
    return TradingCalendar(
        calendar_id="test-market",
        name="Test Market",
        timezone="America/New_York",
        weekly_sessions=(
            WeeklySession(
                weekday=1,
                windows=(
                    SessionWindow(
                        open_time=time(9, 0),
                        close_time=time(12, 0),
                    ),
                ),
            ),
        ),
    )


def make_provider_schedule(
    symbol="EURUSD",
    timeframe=Timeframe.H1,
):
    return ProviderAvailabilitySchedule(
        provider_id="test-provider",
        asset_class=AssetClass.FOREX,
        timeframe=timeframe,
        timezone="America/New_York",
        weekly_availability=(
            WeeklyProviderAvailability(
                weekday=1,
                windows=(
                    ProviderAvailabilityWindow(
                        open_time=time(9, 0),
                        close_time=time(12, 0),
                    ),
                ),
            ),
        ),
        instrument_symbol=symbol,
    )


def make_policy(timeframe=Timeframe.H1):
    return BarBoundaryPolicy(
        policy_id="test-policy",
        timeframe=timeframe,
        alignment_mode=BarAlignmentMode.SESSION,
    )


def make_bar(hour, symbol="EURUSD", timeframe=Timeframe.H1):
    return OHLCVBar(
        symbol=symbol,
        timeframe=timeframe,
        timestamp=datetime(2026, 1, 5, hour, 0, tzinfo=UTC),
        open=Decimal("1.1000"),
        high=Decimal("1.1050"),
        low=Decimal("1.0950"),
        close=Decimal("1.1020"),
        volume=Decimal("100"),
    )


def assess(bars):
    return OHLCVCoverageAssessor().assess(
        instrument=make_instrument(),
        calendar=make_calendar(),
        provider_schedule=make_provider_schedule(),
        policy=make_policy(),
        bars=bars,
        start=datetime(2026, 1, 5, 14, 0, tzinfo=UTC),
        end=datetime(2026, 1, 5, 17, 0, tzinfo=UTC),
    )


def test_complete_coverage():
    result = assess(
        (
            make_bar(14),
            make_bar(15),
            make_bar(16),
        )
    )

    assert result.expected_count == 3
    assert result.observed_expected_count == 3
    assert result.missing_count == 0
    assert result.coverage_ratio == 1.0
    assert result.quality_report.checked_records == 3
    assert result.quality_report.findings == ()
    assert len(result.market_intervals) == 1
    assert len(result.provider_intervals) == 1


def test_missing_bar_reduces_coverage():
    result = assess(
        (
            make_bar(14),
            make_bar(16),
        )
    )

    assert result.expected_count == 3
    assert result.observed_expected_count == 2
    assert result.missing_count == 1
    assert result.coverage_ratio == pytest.approx(2 / 3)
    assert result.quality_report.warning_count == 1

    finding = result.quality_report.findings[0]
    assert finding.code == "MISSING_OHLCV_BAR"
    assert finding.timestamp == datetime(
        2026, 1, 5, 15, 0, tzinfo=UTC
    )


def test_empty_observations_report_all_expected_bars_missing():
    result = assess(())

    assert result.expected_count == 3
    assert result.observed_expected_count == 0
    assert result.missing_count == 3
    assert result.coverage_ratio == 0.0
    assert result.quality_report.checked_records == 0


def test_unexpected_observation_does_not_inflate_coverage():
    result = assess(
        (
            make_bar(13),
            make_bar(14),
            make_bar(15),
        )
    )

    assert result.expected_count == 3
    assert result.observed_expected_count == 2
    assert result.missing_count == 1
    assert result.coverage_ratio == pytest.approx(2 / 3)


def test_wrong_instrument_is_rejected():
    with pytest.raises(ValueError, match="requested instrument"):
        assess((make_bar(14, symbol="GBPUSD"),))


def test_wrong_timeframe_is_rejected():
    with pytest.raises(ValueError, match="requested timeframe"):
        assess((make_bar(14, timeframe=Timeframe.M15),))


def test_duplicate_observation_timestamps_are_rejected():
    with pytest.raises(ValueError, match="unique bar timestamps"):
        assess((make_bar(14), make_bar(14)))


def test_invalid_observation_type_is_rejected():
    with pytest.raises(TypeError, match="OHLCVBar"):
        assess((object(),))


def test_string_observation_input_is_rejected():
    with pytest.raises(TypeError, match="bars"):
        assess("invalid")


def test_no_expected_bars_has_full_vacuous_coverage():
    calendar = TradingCalendar(
        calendar_id="closed-market",
        name="Closed Market",
        timezone="America/New_York",
        weekly_sessions=(),
    )
    provider = ProviderAvailabilitySchedule(
        provider_id="test-provider",
        asset_class=AssetClass.FOREX,
        timeframe=Timeframe.H1,
        timezone="America/New_York",
        weekly_availability=(),
    )

    result = OHLCVCoverageAssessor().assess(
        instrument=make_instrument(),
        calendar=calendar,
        provider_schedule=provider,
        policy=make_policy(),
        bars=(),
        start=datetime(2026, 1, 5, 14, 0, tzinfo=UTC),
        end=datetime(2026, 1, 5, 17, 0, tzinfo=UTC),
    )

    assert result.expected_count == 0
    assert result.observed_expected_count == 0
    assert result.missing_count == 0
    assert result.coverage_ratio == 1.0


def test_assessment_rejects_inconsistent_counts():
    with pytest.raises(
        ValueError,
        match="Observed and missing counts",
    ):
        OHLCVCoverageAssessment(
            instrument_symbol="EURUSD",
            timeframe=Timeframe.H1,
            expected_timestamps=(),
            expected_count=0,
            observed_expected_count=1,
            missing_count=0,
            coverage_ratio=1.0,
            quality_report=DataQualityReport(
                checked_records=0,
            ),
            market_intervals=(),
            provider_intervals=(),
        )


def test_assessment_rejects_invalid_coverage_ratio():
    with pytest.raises(ValueError, match="coverage_ratio"):
        OHLCVCoverageAssessment(
            instrument_symbol="EURUSD",
            timeframe=Timeframe.H1,
            expected_timestamps=(),
            expected_count=0,
            observed_expected_count=0,
            missing_count=0,
            coverage_ratio=0.5,
            quality_report=DataQualityReport(
                checked_records=0,
            ),
            market_intervals=(),
            provider_intervals=(),
        )