"""Tests for expected market data timestamp generation."""

from datetime import date, datetime, time, timezone

import pytest

from atlas_quant.services.market_data.domain.enums import (
    AssetClass,
    Timeframe,
)
from atlas_quant.services.market_data.domain.instruments import Instrument
from atlas_quant.services.market_data.domain.calendar import (
    CalendarException,
    SessionWindow,
    TradingCalendar,
    WeeklySession,
)
from atlas_quant.services.market_data.domain.provider_availability import (
    ProviderAvailabilityException,
    ProviderAvailabilitySchedule,
    ProviderAvailabilityWindow,
    WeeklyProviderAvailability,
)
from atlas_quant.services.market_data.domain.bar_boundaries import (
    BarAlignmentMode,
    BarBoundaryPolicy,
)
from atlas_quant.services.market_data.application.expected_timestamps import (
    ExpectedTimestampGenerator,
)


UTC = timezone.utc


def make_instrument() -> Instrument:
    return Instrument(
        symbol="EURUSD",
        asset_class=AssetClass.FOREX,
    )


def make_calendar(
    weekly_sessions=(),
    exceptions=(),
    timezone_name="America/New_York",
) -> TradingCalendar:
    return TradingCalendar(
        calendar_id="test-market",
        name="Test Market",
        timezone=timezone_name,
        weekly_sessions=weekly_sessions,
        exceptions=exceptions,
    )


def make_provider_schedule(
    weekly_availability=(),
    exceptions=(),
    timeframe=Timeframe.H1,
    timezone_name="America/New_York",
    instrument_symbol="EURUSD",
) -> ProviderAvailabilitySchedule:
    return ProviderAvailabilitySchedule(
        provider_id="test-provider",
        asset_class=AssetClass.FOREX,
        timeframe=timeframe,
        timezone=timezone_name,
        weekly_availability=weekly_availability,
        exceptions=exceptions,
        instrument_symbol=instrument_symbol,
    )


def make_policy(
    timeframe=Timeframe.H1,
    alignment=BarAlignmentMode.SESSION,
    allow_partial_final_bar=False,
    boundary_timezone=None,
    period_start_time=None,
    week_start_day=None,
) -> BarBoundaryPolicy:
    return BarBoundaryPolicy(
        policy_id="test-policy",
        timeframe=timeframe,
        alignment_mode=alignment,
        allow_partial_final_bar=allow_partial_final_bar,
        boundary_timezone=boundary_timezone,
        period_start_time=period_start_time,
        week_start_day=week_start_day,
    )


def make_market_window(
    open_time=time(9, 0),
    close_time=time(17, 0),
    close_day_offset=0,
) -> SessionWindow:
    return SessionWindow(
        open_time=open_time,
        close_time=close_time,
        close_day_offset=close_day_offset,
    )


def make_provider_window(
    open_time=time(9, 0),
    close_time=time(17, 0),
    close_day_offset=0,
) -> ProviderAvailabilityWindow:
    return ProviderAvailabilityWindow(
        open_time=open_time,
        close_time=close_time,
        close_day_offset=close_day_offset,
    )


def make_monday_market(window=None, exceptions=()):
    return make_calendar(
        weekly_sessions=(
            WeeklySession(
                weekday=1,
                windows=(window or make_market_window(),),
            ),
        ),
        exceptions=exceptions,
    )


def make_monday_provider(window=None, exceptions=(), timeframe=Timeframe.H1):
    return make_provider_schedule(
        weekly_availability=(
            WeeklyProviderAvailability(
                weekday=1,
                windows=(window or make_provider_window(),),
            ),
        ),
        exceptions=exceptions,
        timeframe=timeframe,
    )


def generate(
    calendar,
    provider,
    policy,
    start=datetime(2026, 1, 5, 14, 0, tzinfo=UTC),
    end=datetime(2026, 1, 5, 22, 0, tzinfo=UTC),
):
    return ExpectedTimestampGenerator().generate(
        instrument=make_instrument(),
        calendar=calendar,
        provider_schedule=provider,
        policy=policy,
        start=start,
        end=end,
    )


def test_session_alignment_anchors_bars_to_market_session_open():
    result = generate(
        make_monday_market(),
        make_monday_provider(),
        make_policy(),
    )

    assert result.expected_timestamps == tuple(
        datetime(2026, 1, 5, hour, 0, tzinfo=UTC)
        for hour in range(14, 22)
    )


def test_provider_availability_does_not_shift_session_bar_boundaries():
    provider = make_monday_provider(
        window=make_provider_window(
            time(10, 0),
            time(17, 0),
        ),
    )

    result = generate(
        make_monday_market(),
        provider,
        make_policy(),
    )

    assert result.expected_timestamps == tuple(
        datetime(2026, 1, 5, hour, 0, tzinfo=UTC)
        for hour in range(15, 22)
    )


def test_provider_must_cover_the_entire_bar():
    provider = make_monday_provider(
        window=make_provider_window(
            time(9, 30),
            time(17, 0),
        ),
    )

    result = generate(
        make_monday_market(),
        provider,
        make_policy(),
    )

    assert result.expected_timestamps == tuple(
        datetime(2026, 1, 5, hour, 0, tzinfo=UTC)
        for hour in range(15, 22)
    )


def test_partial_final_bar_is_excluded_by_default():
    calendar = make_monday_market(
        window=make_market_window(
            time(9, 0),
            time(16, 30),
        ),
    )
    provider = make_monday_provider(
        window=make_provider_window(
            time(9, 0),
            time(16, 30),
        ),
    )

    result = generate(
        calendar,
        provider,
        make_policy(),
    )

    assert result.expected_timestamps == tuple(
        datetime(2026, 1, 5, hour, 0, tzinfo=UTC)
        for hour in range(14, 21)
    )


def test_partial_final_bar_can_be_enabled_at_market_close():
    calendar = make_monday_market(
        window=make_market_window(
            time(9, 0),
            time(16, 30),
        ),
    )
    provider = make_monday_provider(
        window=make_provider_window(
            time(9, 0),
            time(16, 30),
        ),
    )

    result = generate(
        calendar,
        provider,
        make_policy(allow_partial_final_bar=True),
    )

    assert result.expected_timestamps == tuple(
        datetime(2026, 1, 5, hour, 0, tzinfo=UTC)
        for hour in range(14, 22)
    )


def test_partial_bar_is_not_created_at_provider_closing_time():
    provider = make_monday_provider(
        window=make_provider_window(
            time(9, 0),
            time(16, 30),
        ),
    )

    result = generate(
        make_monday_market(),
        provider,
        make_policy(allow_partial_final_bar=True),
    )

    assert result.expected_timestamps == tuple(
        datetime(2026, 1, 5, hour, 0, tzinfo=UTC)
        for hour in range(14, 21)
    )


def test_overnight_session_keeps_original_session_anchor():
    calendar = make_calendar(
        weekly_sessions=(
            WeeklySession(
                weekday=7,
                windows=(
                    make_market_window(
                        time(22, 0),
                        time(2, 0),
                        close_day_offset=1,
                    ),
                ),
            ),
        ),
    )
    provider = make_provider_schedule(
        weekly_availability=(
            WeeklyProviderAvailability(
                weekday=7,
                windows=(
                    make_provider_window(
                        time(22, 0),
                        time(2, 0),
                        close_day_offset=1,
                    ),
                ),
            ),
        ),
    )

    result = generate(
        calendar,
        provider,
        make_policy(),
        start=datetime(2026, 1, 4, 22, 0, tzinfo=UTC),
        end=datetime(2026, 1, 5, 7, 0, tzinfo=UTC),
    )

    assert result.expected_timestamps == (
        datetime(2026, 1, 5, 3, 0, tzinfo=UTC),
        datetime(2026, 1, 5, 4, 0, tzinfo=UTC),
        datetime(2026, 1, 5, 5, 0, tzinfo=UTC),
        datetime(2026, 1, 5, 6, 0, tzinfo=UTC),
    )


def test_utc_clock_alignment_uses_utc_boundaries():
    result = generate(
        make_monday_market(),
        make_monday_provider(),
        make_policy(alignment=BarAlignmentMode.UTC_CLOCK),
    )

    assert result.expected_timestamps == tuple(
        datetime(2026, 1, 5, hour, 0, tzinfo=UTC)
        for hour in range(14, 22)
    )


def test_calendar_period_daily_boundary_uses_configured_local_time():
    calendar = make_calendar(
        weekly_sessions=(
            WeeklySession(
                weekday=1,
                windows=(make_market_window(),),
            ),
            WeeklySession(
                weekday=2,
                windows=(make_market_window(),),
            ),
        ),
    )
    provider = make_provider_schedule(
        weekly_availability=(
            WeeklyProviderAvailability(
                weekday=1,
                windows=(make_provider_window(),),
            ),
            WeeklyProviderAvailability(
                weekday=2,
                windows=(make_provider_window(),),
            ),
        ),
        timeframe=Timeframe.D1,
    )
    policy = make_policy(
        timeframe=Timeframe.D1,
        alignment=BarAlignmentMode.CALENDAR_PERIOD,
        boundary_timezone="America/New_York",
        period_start_time=time(9, 0),
    )

    result = generate(
        calendar,
        provider,
        policy,
        start=datetime(2026, 1, 5, 14, 0, tzinfo=UTC),
        end=datetime(2026, 1, 6, 22, 0, tzinfo=UTC),
    )

    assert result.expected_timestamps == (
        datetime(2026, 1, 5, 14, 0, tzinfo=UTC),
        datetime(2026, 1, 6, 14, 0, tzinfo=UTC),
    )


def test_monthly_bar_checks_provider_coverage_across_entire_month():
    calendar = make_calendar(
        weekly_sessions=(
            WeeklySession(
                weekday=1,
                windows=(make_market_window(),),
            ),
            WeeklySession(
                weekday=2,
                windows=(make_market_window(),),
            ),
            WeeklySession(
                weekday=3,
                windows=(make_market_window(),),
            ),
            WeeklySession(
                weekday=4,
                windows=(make_market_window(),),
            ),
            WeeklySession(
                weekday=5,
                windows=(make_market_window(),),
            ),
        ),
    )

    provider = make_provider_schedule(
        weekly_availability=(
            WeeklyProviderAvailability(
                weekday=1,
                windows=(make_provider_window(),),
            ),
            WeeklyProviderAvailability(
                weekday=2,
                windows=(make_provider_window(),),
            ),
            WeeklyProviderAvailability(
                weekday=3,
                windows=(make_provider_window(),),
            ),
            WeeklyProviderAvailability(
                weekday=4,
                windows=(make_provider_window(),),
            ),
            WeeklyProviderAvailability(
                weekday=5,
                windows=(make_provider_window(),),
            ),
        ),
        exceptions=(
            ProviderAvailabilityException(
                availability_date=date(2026, 1, 5),
                reason="Provider outage",
            ),
        ),
        timeframe=Timeframe.MN1,
    )

    policy = make_policy(
        timeframe=Timeframe.MN1,
        alignment=BarAlignmentMode.CALENDAR_PERIOD,
        boundary_timezone="America/New_York",
        period_start_time=time(0, 0),
    )

    result = generate(
        calendar,
        provider,
        policy,
        start=datetime(2026, 1, 1, 5, 0, tzinfo=UTC),
        end=datetime(2026, 1, 2, 5, 0, tzinfo=UTC),
    )

    assert result.expected_timestamps == ()


def test_generator_rejects_naive_start_datetime():
    with pytest.raises(ValueError, match="start must be timezone-aware"):
        generate(
            make_monday_market(),
            make_monday_provider(),
            make_policy(),
            start=datetime(2026, 1, 5, 14, 0),
        )


def test_generator_rejects_reversed_datetime_range():
    with pytest.raises(ValueError, match="end must be later"):
        generate(
            make_monday_market(),
            make_monday_provider(),
            make_policy(),
            start=datetime(2026, 1, 5, 22, 0, tzinfo=UTC),
            end=datetime(2026, 1, 5, 14, 0, tzinfo=UTC),
        )


def test_generator_rejects_provider_timeframe_mismatch():
    provider = make_monday_provider(timeframe=Timeframe.M5)

    with pytest.raises(ValueError, match="timeframe"):
        generate(
            make_monday_market(),
            provider,
            make_policy(),
        )


def test_generator_rejects_instrument_symbol_mismatch():
    provider = make_provider_schedule(
        weekly_availability=(
            WeeklyProviderAvailability(
                weekday=1,
                windows=(make_provider_window(),),
            ),
        ),
        instrument_symbol="GBPUSD",
    )

    with pytest.raises(ValueError, match="instrument symbol"):
        generate(
            make_monday_market(),
            provider,
            make_policy(),
        )


def test_generator_rejects_asset_class_mismatch():
    provider = ProviderAvailabilitySchedule(
        provider_id="test-provider",
        asset_class=AssetClass.EQUITY,
        timeframe=Timeframe.H1,
        timezone="America/New_York",
        weekly_availability=(
            WeeklyProviderAvailability(
                weekday=1,
                windows=(make_provider_window(),),
            ),
        ),
    )

    with pytest.raises(ValueError, match="asset class"):
        generate(
            make_monday_market(),
            provider,
            make_policy(),
        )